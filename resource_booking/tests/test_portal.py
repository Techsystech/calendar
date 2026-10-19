# Copyright 2021 Tecnativa - Jairo Llopis
# Copyright 2023 Tecnativa - Carolina Fernandez
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from unittest.mock import patch
from urllib.parse import urlencode

from freezegun import freeze_time
from lxml.html import fromstring

from odoo.exceptions import ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import HttpCase

from .common import create_test_data


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class PortalCase(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)

        cls.user_portal = new_test_user(
            cls.env, login="ptl", password="ptl", groups="base.group_portal"
        )
        cls.user_no_booking_access = new_test_user(
            cls.env,
            login="no_booking_access",
            password="no_booking_access",
            groups="base.group_user",
        )
        cls.user_manager = new_test_user(
            cls.env,
            login="mgr",
            password="mgr",
            groups="base.group_user,resource_booking.group_manager",
        )

    def _url_xml(self, url, data=None, timeout=10):
        """Open an URL and return the lxml etree object resulting from its content."""
        response = self.url_open(url, data, timeout=timeout)
        return fromstring(response.content)

    def test_portal_no_bookings(self):
        self.start_tour("/", "resource_booking_ptl_tour", login="ptl")

    def test_portal_list_with_bookings(self):
        # Create one pending booking
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.user_portal.partner_id.id)],
                "type_id": self.rbt.id,
            }
        )
        self.start_tour("/", "resource_booking_ptl2_tour", login="ptl")

    def test_portal_home_booking_counter_without_booking_access(self):
        self.authenticate("no_booking_access", "no_booking_access")

        portal_home = self.url_open("/my")
        self.assertEqual(portal_home.status_code, 200)
        self.assertNotIn(b"You are not allowed", portal_home.content)

        counters = self.make_jsonrpc_request(
            "/my/counters", {"counters": ["booking_count"]}
        )
        self.assertEqual(counters["booking_count"], 0)

        bookings_page = self.url_open("/my/bookings")
        self.assertEqual(bookings_page.status_code, 200)
        self.assertIn(b"There are currently no bookings", bookings_page.content)

    def test_portal_home_booking_counter_with_booking_access(self):
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.user_portal.partner_id.id)],
                "type_id": self.rbt.id,
            }
        )
        self.authenticate("ptl", "ptl")

        counters = self.make_jsonrpc_request(
            "/my/counters", {"counters": ["booking_count"]}
        )
        self.assertEqual(counters["booking_count"], 1)

    def test_portal_list_token_generation_does_not_sync_meeting(self):
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.user_portal.partner_id.id)],
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "start": datetime(2021, 3, 1, 8),
            }
        )
        self.assertTrue(booking.meeting_id)
        booking.access_token = False

        with patch.object(
            type(booking),
            "_sync_meeting",
            side_effect=AssertionError("access_token write should not sync meeting"),
        ):
            portal_url = booking.get_portal_url()

        self.assertTrue(booking.access_token)
        self.assertIn("access_token=", portal_url)

    def test_portal_manager_invalid_slots_leave_original_unchanged(self):
        self.rbt.write({"min_advance_booking_hours": 72, "max_advance_booking_days": 4})
        booking = self.env["resource.booking"].create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 10:00:00",
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
            }
        )
        url = booking.get_portal_url(suffix="/confirm")
        original = (
            booking.start,
            booking.stop,
            booking.combination_id,
            booking.meeting_id,
            booking.state,
        )
        self.authenticate("mgr", "mgr")
        for when in (
            "invalid",
            "2021-03-01T10:00:00",
            "2021-03-01T10:15:00Z",
            "2021-03-01T08:00:00Z",
            "2021-03-02T10:00:00Z",
        ):
            with self.subTest(when=when):
                response = self.url_open(url + "&" + urlencode({"when": when}))
                self.assertEqual(response.status_code, 200)
                self.assertIn("error=", response.url)
                booking.invalidate_recordset()
                self.assertEqual(
                    (
                        booking.start,
                        booking.stop,
                        booking.combination_id,
                        booking.meeting_id,
                        booking.state,
                    ),
                    original,
                )
        response = self.url_open(
            url + "&" + urlencode({"when": "2021-03-01T11:00:00Z"})
        )
        self.assertEqual(response.status_code, 200)
        booking.invalidate_recordset()
        self.assertEqual(booking.start, datetime(2021, 3, 1, 11))
        self.assertEqual(booking.state, "confirmed")

    def test_portal_confirmation_rolls_back_assignment_and_attendance(self):
        booking = self.env["resource.booking"].create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 10:00:00",
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
            }
        )
        url = booking.get_portal_url(suffix="/confirm")
        confirm = type(booking).action_confirm

        def failing_confirm(records):
            confirm(records)
            raise ValidationError("Confirmation failed")

        with patch.object(type(booking), "action_confirm", failing_confirm):
            response = self.url_open(
                url + "&" + urlencode({"when": "2021-03-01T11:00:00Z"})
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("error=", response.url)
        booking.invalidate_recordset()
        booking.meeting_id.invalidate_recordset()
        booking.meeting_id.attendee_ids.invalidate_recordset()
        self.assertEqual(booking.start, datetime(2021, 3, 1, 10))
        self.assertEqual(booking.meeting_id.start, booking.start)
        self.assertEqual(booking.combination_id, self.rbcs[0])
        self.assertEqual(booking.state, "scheduled")
        requester = booking.meeting_id.attendee_ids.filtered(
            lambda attendee: attendee.partner_id == self.partner
        )
        self.assertEqual(requester.state, "needsAction")

    def test_portal_cannot_move_frozen_booking_into_future(self):
        self.rbt.resource_calendar_id = self.r_calendars[3]
        booking = self.env["resource.booking"].create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-02-26 12:00:00",
                "combination_id": self.rbcs[3].id,
                "combination_auto_assign": False,
            }
        )
        response = self.url_open(
            booking.get_portal_url(suffix="/confirm")
            + "&"
            + urlencode({"when": "2021-03-05T12:00:00Z"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("error=", response.url)
        booking.invalidate_recordset()
        booking.meeting_id.invalidate_recordset()
        self.assertEqual(booking.start, datetime(2021, 2, 26, 12))
        self.assertEqual(booking.meeting_id.start, booking.start)
        self.assertEqual(booking.state, "scheduled")

    def test_portal_scheduling_conflict(self):
        """Produce a scheduling conflict and see how UI behaves.

        This test would be better as a tour, but since there are a few back and
        forth actions among backend and frontend, and among distinct portal
        users, it seemed easier to do it completely on python.
        """
        # Set RBT to have only 1 combination available: the one for Mondays
        self.rbt.combination_rel_ids[1:].unlink()
        # One booking for portal user, another for a partner without user
        bookings = self.env["resource.booking"].create(
            [
                {
                    "partner_ids": [(4, self.user_portal.partner_id.id)],
                    "type_id": self.rbt.id,
                    "duration": 1,
                },
                {
                    "partner_ids": [(4, self.partner.id)],
                    "type_id": self.rbt.id,
                    "location": "Office 2",
                    "videocall_location": "Videocall Office 2",
                },
            ]
        )
        booking_public = bookings[1]
        # We assume they were invited by email and clicked on their links
        portal_url, public_url = (one.get_portal_url() for one in bookings)
        # Portal guy goes to scheduling page
        portal_page = self._url_xml(portal_url)
        self.assertTrue(portal_page.cssselect('.o_rb_status_pill:contains("Pending")'))
        self.assertTrue(
            portal_page.cssselect(
                '.o_rb_detail_row:contains("Duration"):contains("1 hr")'
            )
        )
        self.assertTrue(
            portal_page.cssselect(
                '.o_rb_detail_row:contains("Location"):contains("Main office")'
            )
        )
        self.assertTrue(
            portal_page.cssselect(
                '.o_rb_detail_row:contains("Meeting link")'
                ':contains("Videocall Main office")'
            )
        )
        link = portal_page.cssselect('a:contains("Schedule")')[0]
        portal_url = link.get("href")
        portal_page = self._url_xml(portal_url)
        # Nothing free on February, he goes to March
        self.assertTrue(
            portal_page.cssselect(".o_booking_calendar:contains('February 2021')")
        )
        self.assertTrue(
            portal_page.cssselect(
                ".o_booking_calendar td"
                ":contains('All times are displayed using this timezone:')"
                ":contains('UTC')"
            )
        )
        self.assertFalse(portal_page.cssselect(".o_booking_calendar .dropdown"))
        self.assertFalse(portal_page.cssselect(".o_booking_calendar form"))
        link = portal_page.cssselect('a[title="Next month"]')[0]
        portal_url = link.get("href")
        portal_page = self._url_xml(portal_url)
        self.assertTrue(
            portal_page.cssselect(".o_booking_calendar:contains('March 2021')")
        )
        self.assertTrue(portal_page.cssselect(".o_booking_calendar .dropdown"))
        self.assertTrue(portal_page.cssselect(".o_booking_calendar form"))
        # Public guy does the same
        public_page = self._url_xml(public_url)
        self.assertTrue(
            public_page.cssselect(
                '.o_rb_detail_row:contains("Duration"):contains("30 min")'
            )
        )
        self.assertTrue(
            public_page.cssselect(
                '.o_rb_detail_row:contains("Location"):contains("Office 2")'
            )
        )
        self.assertTrue(
            public_page.cssselect(
                '.o_rb_detail_row:contains("Meeting link")'
                ':contains("Videocall Office 2")'
            )
        )
        self.assertTrue(public_page.cssselect('.o_rb_status_pill:contains("Pending")'))
        link = public_page.cssselect('a:contains("Schedule")')[0]
        public_url = link.get("href")
        public_page = self._url_xml(public_url)
        self.assertTrue(
            public_page.cssselect(".o_booking_calendar:contains('February 2021')")
        )
        self.assertTrue(
            public_page.cssselect(
                ".o_booking_calendar td"
                ":contains('All times are displayed using this timezone:')"
                ":contains('UTC')"
            )
        )
        self.assertFalse(public_page.cssselect(".o_booking_calendar .dropdown"))
        self.assertFalse(public_page.cssselect(".o_booking_calendar form"))
        link = public_page.cssselect('a[title="Next month"]')[0]
        public_url = link.get("href")
        public_page = self._url_xml(public_url)
        self.assertTrue(
            public_page.cssselect(".o_booking_calendar:contains('March 2021')")
        )
        self.assertTrue(public_page.cssselect(".o_booking_calendar .dropdown"))
        self.assertTrue(public_page.cssselect(".o_booking_calendar form"))
        # Public guy makes reservation next Monday at 10:00
        slot = datetime(2021, 3, 1, 10).timestamp()
        selector_10am = (
            "#dropdown-trigger-2021-03-01 "
            "+ .slots-dropdown .dropdown-item:contains('10:00')"
        )
        selector_1030am = (
            "#dropdown-trigger-2021-03-01 "
            "+ .slots-dropdown .dropdown-item:contains('10:30')"
        )
        self.assertTrue(public_page.cssselect(selector_10am))
        self.assertTrue(public_page.cssselect(selector_1030am))
        form = public_page.cssselect("form#modal-confirm-%d" % slot)[0]
        public_url = form.get("action")
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        public_page = self._url_xml(public_url, data)
        # Public guy's reservation succeeded
        self.assertTrue(
            public_page.cssselect('.o_rb_status_pill:contains("Confirmed")')
        )
        self.assertTrue(
            public_page.cssselect(
                '.o_rb_detail_row:contains("Booked resources")'
                ':contains("Material resource for Mon")'
                ':contains("User User 0")'
            )
        )
        self.assertTrue(
            public_page.cssselect(
                '.o_rb_detail_row:contains("Location"):contains("Office 2")'
            )
        )
        self.assertIn(
            booking_public.with_context(tz="UTC")._get_portal_start_display(),
            public_page.cssselect('.o_rb_detail_row:contains("Date and time")')[
                0
            ].text_content(),
        )
        # Public guy's booking and related meeting are OK in backend
        booking_public.invalidate_model()
        self.assertEqual(booking_public.state, "confirmed")
        self.assertEqual(len(booking_public.meeting_id.attendee_ids), 2)
        for attendee in booking_public.meeting_id.attendee_ids:
            self.assertTrue(attendee.partner_id)
            self.assertIn(
                attendee.partner_id,
                self.partner | self.users[0].partner_id,
            )
            self.assertEqual(
                attendee.state,
                "accepted" if attendee.partner_id == self.partner else "needsAction",
            )
        # At the same time, portal guy tries to reserve the same slot, which
        # appears as free to him due to the race condition we just created
        self.assertTrue(portal_page.cssselect(selector_10am))
        self.assertTrue(portal_page.cssselect(selector_1030am))
        form = portal_page.cssselect("form#modal-confirm-%d" % slot)[0]
        portal_url = form.get("action")
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        portal_page = self._url_xml(portal_url, data)
        # He's back on the March calendar view, with an error message
        self.assertTrue(
            portal_page.cssselect(
                ".alert-danger:contains('The chosen schedule is no longer available.')"
            )
        )
        self.assertTrue(
            portal_page.cssselect(".o_booking_calendar:contains('March 2021')")
        )
        self.assertTrue(portal_page.cssselect(".o_booking_calendar .dropdown"))
        self.assertTrue(portal_page.cssselect(".o_booking_calendar form"))
        # He can't select that slot anymore, so he books it 30 minutes later
        self.assertFalse(portal_page.cssselect(selector_10am))
        self.assertTrue(portal_page.cssselect(selector_1030am))
        slot = datetime(2021, 3, 1, 10, 30).timestamp()
        self.assertTrue(portal_page.cssselect("#dropdown-trigger-2021-03-08"))
        form = portal_page.cssselect("form#modal-confirm-%d" % slot)[0]
        portal_url = form.get("action")
        data = {
            element.get("name"): element.get("value")
            for element in form.cssselect("input")
        }
        portal_page = self._url_xml(portal_url, data)
        # Portal guy's reservation succeeded
        self.assertTrue(
            portal_page.cssselect('.o_rb_status_pill:contains("Confirmed")')
        )
        self.assertTrue(
            portal_page.cssselect(
                '.o_rb_detail_row:contains("Booked resources")'
                ':contains("Material resource for Mon")'
                ':contains("User User 0")'
            )
        )
        self.assertTrue(
            portal_page.cssselect(
                '.o_rb_detail_row:contains("Location"):contains("Main office")'
            )
        )
        self.assertIn(
            bookings[0].with_context(tz="UTC")._get_portal_start_display(),
            portal_page.cssselect('.o_rb_detail_row:contains("Date and time")')[
                0
            ].text_content(),
        )
        # Portal guy cancels
        link = portal_page.cssselect('a:contains("Cancel this booking")')[0]
        portal_url = link.get("href")
        portal_page = self._url_xml(portal_url)
        self.assertTrue(portal_page.cssselect(".oe_login_form"))

    def test_portal_home_no_resource_booking_access(self):
        """Internal users without resource_booking permissions can still view /my.

        Portal users have read access via the dedicated ACL, but a plain
        internal user (e.g. someone added by another website-facing module)
        has no row in ir.model.access.csv for resource.booking, so the
        unguarded search_count in _prepare_home_portal_values used to raise
        AccessError and break the entire portal home page.
        """
        plain = new_test_user(
            self.env,
            login="plain_internal",
            password="plain_internal",
            groups="base.group_user",
        )
        self.assertFalse(
            self.env["resource.booking"].with_user(plain).has_access("read"),
            "test precondition: plain internal user must lack booking read",
        )
        self.authenticate("plain_internal", "plain_internal")
        response = self.url_open("/my", timeout=10)
        self.assertEqual(response.status_code, 200)
        # Positive marker: the standard portal-home CSS hook is present, so
        # the layout actually rendered rather than degrading to an error page.
        tree = fromstring(response.text)
        self.assertTrue(
            tree.cssselect(".o_portal_my_home, .o_portal_wrap"),
            "Portal home wrapper element should render even without "
            "resource.booking read access",
        )

    def test_portal_my_bookings_no_access_renders_empty(self):
        """An internal user without booking read access lands on an empty list."""
        plain = new_test_user(
            self.env,
            login="plain_internal2",
            password="plain_internal2",
            groups="base.group_user",
        )
        self.assertFalse(
            self.env["resource.booking"].with_user(plain).has_access("read"),
            "test precondition: plain internal user must lack booking read",
        )
        self.authenticate("plain_internal2", "plain_internal2")
        response = self.url_open("/my/bookings", timeout=10)
        self.assertEqual(response.status_code, 200)
        # Positive marker: the empty-state alert from the QWeb template
        # confirms the bookings listing rendered with zero rows rather than
        # falling through to an error page.
        self.assertIn(
            "There are currently no bookings for your account.",
            response.text,
        )
