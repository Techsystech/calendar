# Copyright 2025 Ledo Enterprises LLC - Don Kendall
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json
from datetime import datetime
from unittest.mock import patch

from freezegun import freeze_time
from lxml.html import fromstring

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.resource_booking.tests.common import create_test_data
from odoo.addons.website_appointment_booking.controllers.main import (
    WebsiteAppointmentBooking,
)


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class TestWebsiteAppointmentBooking(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.rbt.write(
            {
                "website_published": True,
                "website_slug": "test-booking",
                "website_card_image": base64.b64encode(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")),
                "location": "Main office",
            }
        )

    def _url_xml(self, url, data=None, timeout=10):
        """Open a URL and return its parsed lxml tree."""
        response = self.url_open(url, data, timeout=timeout)
        return fromstring(response.content)

    def _get_csrf_token(self, page):
        """Extract CSRF token from a page's hidden input."""
        inputs = page.cssselect('input[name="csrf_token"]')
        if inputs:
            return inputs[0].get("value")
        return ""

    def _set_browser_tz_cookie(self, timezone_name):
        """Set or clear the browser timezone cookie for the test opener."""
        if timezone_name:
            self.opener.cookies["tz"] = timezone_name
        elif "tz" in self.opener.cookies:
            del self.opener.cookies["tz"]

    def test_landing_page_lists_published_booking_cards(self):
        """/book lists published booking types as cards with image, link and avatars."""
        page = self._url_xml("/book")
        cards = page.cssselect(".o_wab_booking_card")
        self.assertTrue(cards)
        test_cards = [
            card
            for card in cards
            if card.cssselect('a[href="/book/test-booking"]:contains("Book now")')
        ]
        self.assertEqual(len(test_cards), 1)
        card = test_cards[0]
        self.assertTrue(card.cssselect(":contains('Test resource booking type')"))
        self.assertTrue(card.cssselect('img[src*="/web/image/resource.booking.type/"]'))
        # Avatars are auto-computed from resources with users; test data may not have images
        self.assertTrue(card.cssselect(".o_wab_card_avatars"))
        self.assertIn("col-lg-3", card.getparent().get("class", ""))
        self.assertTrue(card.cssselect(".o_wab_card_meta_item:contains('30 min')"))
        self.assertTrue(card.cssselect(".o_wab_card_meta_item:contains('Main office')"))

    def test_landing_page_excludes_unpublished_slug(self):
        """Published flag controls /book card visibility without deleting slug data."""
        page = self._url_xml("/book")
        self.assertFalse(page.cssselect('a[href="/book/unpublished-type"]'))

    def test_landing_page_has_standard_website_page_publish_record(self):
        """The /book route has a standard website.page record for editor publishing."""
        website_page = self.env.ref(
            "website_appointment_booking.booking_landing_website_page"
        )
        self.assertEqual(website_page.url, "/book")
        self.assertEqual(website_page.view_id.key, "website_appointment_booking.booking_landing_page")
        self.assertTrue(website_page.website_published)

    def test_booking_type_website_url_points_to_public_slug(self):
        """Booking types expose the standard website URL used by the smart button."""
        self.assertEqual(self.rbt.website_url, "/book/test-booking")

    def test_unpublished_returns_404(self):
        """Unpublished booking types are not accessible.

        Uses a slug that has never been published, since changes made in test
        methods are not visible to the HTTP server thread in HttpCase.
        """
        response = self.url_open("/book/unpublished-type")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_slug_returns_404(self):
        """Unknown slugs return 404."""
        response = self.url_open("/book/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_booking_page_renders(self):
        """Published booking page renders with calendar."""
        page = self._url_xml("/book/test-booking")
        # The page should show the booking type name
        self.assertTrue(
            page.cssselect(".o_wab_title:contains('Test resource booking type')")
        )
        # Duration pill should be present
        self.assertTrue(page.cssselect(".o_wab_meta_pill:contains('30 min')"))

    def test_booking_page_prefills_logged_in_contact_fields(self):
        """Logged-in users see account contact values prefilled in the booking form."""
        admin = self.env.ref("base.user_admin")
        admin.partner_id.write(
            {
                "name": "Admin Booker",
                "email": "admin-booker@example.com",
                "phone": "+1 555-7777",
            }
        )
        self.authenticate("admin", "admin")
        page = self._url_xml("/book/test-booking/2021/3")
        self.assertEqual(page.cssselect('#o_wab_name')[0].get("value"), "Admin Booker")
        self.assertEqual(
            page.cssselect('#o_wab_email')[0].get("value"), "admin-booker@example.com"
        )
        self.assertEqual(page.cssselect('#o_wab_phone')[0].get("value"), "+1 555-7777")

    def test_booking_page_leaves_public_contact_fields_blank(self):
        """Public visitors still get empty booking contact fields."""
        page = self._url_xml("/book/test-booking/2021/3")
        self.assertIsNone(page.cssselect('#o_wab_name')[0].get("value"))
        self.assertIsNone(page.cssselect('#o_wab_email')[0].get("value"))
        self.assertIsNone(page.cssselect('#o_wab_phone')[0].get("value"))

    def test_booking_page_february_no_slots(self):
        """February 2021 has no available Monday/Tuesday slots (too close)."""
        page = self._url_xml("/book/test-booking")
        # February should have no available slots (within modification deadline)
        # Month nav should still be visible so users can go back/forth
        self.assertTrue(page.cssselect(".o_wab_month_nav"))
        self.assertTrue(page.cssselect(".o_wab_month_label:contains('February 2021')"))
        self.assertTrue(
            page.cssselect(".o_wab_empty_month:contains('No available slots')")
        )
        self.assertTrue(
            page.cssselect(".o_wab_next_month_btn:contains('Try next month')")
        )
        self.assertTrue(
            page.cssselect(
                ".o_wab_next_month_btn.d-inline-flex.align-items-center.gap-2 "
                ".fa-arrow-right[aria-hidden='true']"
            )
        )

    def test_booking_page_march_has_slots(self):
        """March 2021 should have available slots on Mondays and Tuesdays."""
        page = self._url_xml("/book/test-booking/2021/3")
        self.assertTrue(page.cssselect(".o_wab_month_label:contains('March 2021')"))
        # Should have slot data in hidden JSON element
        slot_data_el = page.cssselect("#o_wab_slot_data")
        self.assertTrue(slot_data_el)
        # Calendar table should be present
        self.assertTrue(page.cssselect(".o_wab_table"))

    def test_booking_page_navigation(self):
        """Month navigation links work."""
        page = self._url_xml("/book/test-booking/2021/3")
        # Should have a next month link
        next_links = page.cssselect('a[href*="/book/test-booking/2021/4"]')
        self.assertTrue(next_links)

    def test_booking_page_timezone_selector_updates_slot_data(self):
        """Timezone query selects the timezone and recomputes displayed slots."""
        default_page = self._url_xml("/book/test-booking/2021/3")
        pacific_page = self._url_xml("/book/test-booking/2021/3?tz=US/Pacific")
        default_slot_data = json.loads(
            default_page.cssselect("#o_wab_slot_data")[0].get("data-slots")
        )
        pacific_slot_data = json.loads(
            pacific_page.cssselect("#o_wab_slot_data")[0].get("data-slots")
        )
        selected_option = pacific_page.cssselect(
            '#o_wab_timezone option[value="US/Pacific"][selected]'
        )
        self.assertTrue(selected_option)
        self.assertEqual(
            pacific_page.cssselect("#o_wab_slot_data")[0].get("data-selected-tz"),
            "US/Pacific",
        )
        self.assertNotEqual(default_slot_data[0]["time"], pacific_slot_data[0]["time"])

    def test_booking_page_prefers_browser_timezone_cookie(self):
        """Browser tz cookie should be used as the default selected timezone."""
        self._set_browser_tz_cookie("US/Central")
        page = self._url_xml("/book/test-booking/2021/3")
        selected_option = page.cssselect(
            '#o_wab_timezone option[value="US/Central"][selected]'
        )
        self.assertTrue(selected_option)
        self.assertEqual(
            page.cssselect("#o_wab_slot_data")[0].get("data-selected-tz"),
            "US/Central",
        )

    def test_booking_page_query_timezone_overrides_cookie(self):
        """Explicit timezone query should win over the browser tz cookie."""
        self._set_browser_tz_cookie("US/Central")
        page = self._url_xml("/book/test-booking/2021/3?tz=US/Pacific")
        selected_option = page.cssselect(
            '#o_wab_timezone option[value="US/Pacific"][selected]'
        )
        self.assertTrue(selected_option)
        self.assertEqual(
            page.cssselect("#o_wab_slot_data")[0].get("data-selected-tz"),
            "US/Pacific",
        )

    def test_booking_page_slots_endpoint_returns_timezone_slots(self):
        """AJAX slot endpoint should return recomputed slot data for a timezone."""
        default_response = self.url_open("/book/test-booking/slots?year=2021&month=3")
        pacific_response = self.url_open(
            "/book/test-booking/slots?year=2021&month=3&tz=US/Pacific"
        )
        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(pacific_response.status_code, 200)
        default_data = default_response.json()
        pacific_data = pacific_response.json()
        self.assertEqual(pacific_data["selected_tz"], "US/Pacific")
        self.assertTrue(default_data["slot_data"])
        self.assertTrue(pacific_data["slot_data"])
        self.assertNotEqual(
            default_data["slot_data"][0]["time"],
            pacific_data["slot_data"][0]["time"],
        )

    def test_booking_page_navigation_preserves_timezone(self):
        """Month navigation keeps the selected timezone query string."""
        page = self._url_xml("/book/test-booking/2021/3?tz=US/Pacific")
        next_links = page.cssselect('a[href*="/book/test-booking/2021/4?"]')
        self.assertTrue(next_links)
        self.assertIn("tz=US%2FPacific", next_links[0].get("href"))

    def test_booking_page_renders_resource_selector(self):
        """Booking page shows no-preference plus ordered resource buttons."""
        page = self._url_xml("/book/test-booking/2021/3")
        buttons = page.cssselect(".o_wab_combination_btn")
        expected_options = [("", "No preference")] + [
            (str(rel.combination_id.id), rel.combination_id.name)
            for rel in self.rbt.combination_rel_ids.sorted("sequence")
        ]
        self.assertEqual(len(buttons), len(expected_options))
        for button, (expected_id, expected_name) in zip(buttons, expected_options, strict=True):
            self.assertEqual(button.get("data-combination-id"), expected_id)
            self.assertEqual(button.text_content().strip(), expected_name)
        self.assertIn("active", buttons[0].get("class", ""))

    def test_booking_page_slots_endpoint_filters_selected_resource(self):
        """Selected resource combination should only expose its own slot dates."""
        response = self.url_open(
            f"/book/test-booking/slots?year=2021&month=3&combination_id={self.rbcs[0].id}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["selected_combination_id"], self.rbcs[0].id)
        self.assertIn("2021-03-01", data["available_dates"])
        self.assertNotIn("2021-03-02", data["available_dates"])

    def test_confirm_persists_selected_resource_combination(self):
        """Submitting a selected resource stores it on the confirmed booking."""
        page = self._url_xml(
            f"/book/test-booking/2021/3?combination_id={self.rbcs[0].id}"
        )
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Specific Visitor",
            "email": "specific@example.com",
            "phone": "+1 555-0100",
            "when": "2021-03-01T10:00:00+00:00",
            "combination_id": str(self.rbcs[0].id),
        }
        response = self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        self.assertIn("/book/test-booking/success", response.url)
        booking = self.env["resource.booking"].search(
            [
                ("type_id", "=", self.rbt.id),
                ("partner_ids.email", "=", "specific@example.com"),
            ],
            limit=1,
        )
        self.assertTrue(booking)
        self.assertEqual(booking.combination_id, self.rbcs[0])
        self.assertFalse(booking.combination_auto_assign)

    def test_confirm_creates_booking(self):
        """Successful form submission creates a confirmed booking."""
        # First load the booking page to get a session and CSRF token
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Test Visitor",
            "email": "visitor@example.com",
            "phone": "+1 555-0101",
            "when": "2021-03-01T10:00:00+00:00",
        }
        response = self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        # Should redirect to success page
        self.assertIn("/book/test-booking/success", response.url)
        # Verify booking was created in backend
        booking = self.env["resource.booking"].search(
            [
                ("type_id", "=", self.rbt.id),
                ("partner_ids.email", "=", "visitor@example.com"),
            ]
        )
        self.assertTrue(booking)
        self.assertEqual(booking.state, "confirmed")
        self.assertTrue(booking.meeting_id)
        # Verify partner was created
        partner = self.env["res.partner"].search(
            [("email", "=ilike", "visitor@example.com")]
        )
        self.assertTrue(partner)
        self.assertEqual(partner.name, "Test Visitor")
        self.assertEqual(partner.phone, "+1 555-0101")

    def test_confirm_saves_discussion_note_on_booking(self):
        """Submitted discussion text should be stored on the booking description."""
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Discussion Visitor",
            "email": "discussion@example.com",
            "phone": "+1 555-0106",
            "discussion": "Talk about payroll automation\nand calendar reminders.",
            "when": "2021-03-01T10:00:00+00:00",
        }
        response = self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        self.assertIn("/book/test-booking/success", response.url)
        booking = self.env["resource.booking"].search(
            [
                ("type_id", "=", self.rbt.id),
                ("partner_ids.email", "=", "discussion@example.com"),
            ],
            limit=1,
        )
        self.assertTrue(booking)
        self.assertEqual(
            booking.description,
            "<p>Talk about payroll automation<br>and calendar reminders.</p>",
        )

    def test_confirm_combines_discussion_with_requester_advice(self):
        """Portal discussion and backend requester advice should both appear in the meeting."""
        self.rbt.requester_advice = "Please arrive 10 minutes early."
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Advice Visitor",
            "email": "advice@example.com",
            "phone": "+1 555-0107",
            "discussion": "I want to discuss retirement planning.",
            "when": "2021-03-01T10:00:00+00:00",
        }
        response = self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        self.assertIn("/book/test-booking/success", response.url)
        booking = self.env["resource.booking"].search(
            [
                ("type_id", "=", self.rbt.id),
                ("partner_ids.email", "=", "advice@example.com"),
            ],
            limit=1,
        )
        self.assertTrue(booking)
        self.assertTrue(booking.meeting_id)
        self.assertIn("retirement planning", booking.meeting_id.description)
        self.assertIn("arrive 10 minutes early", booking.meeting_id.description)

    def test_confirm_missing_phone(self):
        """Submitting without a phone redirects with error."""
        page = self._url_xml("/book/test-booking/2021/3?tz=US/Pacific")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Test User",
            "email": "test@example.com",
            "phone": "",
            "when": "2021-03-01T10:00:00+00:00",
            "tz": "US/Pacific",
        }
        response = self.url_open("/book/test-booking/confirm", data=data)
        self.assertIn("error=", response.url)
        self.assertIn("tz=US%2FPacific", response.url)

    def test_confirm_missing_when(self):
        """Submitting with missing datetime redirects with error."""
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Test User",
            "email": "test@example.com",
            "phone": "+1 555-0102",
            "when": "",
        }
        response = self.url_open("/book/test-booking/confirm", data=data)
        self.assertIn("error=", response.url)

    def test_confirm_invalid_slots_do_not_write_contacts_or_bookings(self):
        self.rbt.write({"min_advance_booking_hours": 72, "max_advance_booking_days": 4})
        partner = self.env["res.partner"].create(
            {
                "name": "Untouched contact",
                "email": "invalid-slot@example.com",
            }
        )
        self.authenticate("admin", "admin")
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        before = self.env["resource.booking"].search_count(
            [("type_id", "=", self.rbt.id)]
        )
        # Avoid the independent anti-spam throttle hiding slot validation failures.
        with patch.object(
            WebsiteAppointmentBooking, "_check_rate_limit", return_value=(True, None)
        ):
            for when in (
                "invalid",
                "2021-03-01T10:00:00",
                "2021-03-01T10:15:00Z",
                "2021-03-01T08:00:00Z",
                "2021-03-02T10:00:00Z",
                "2021-03-01T17:00:00Z",
            ):
                with self.subTest(when=when):
                    response = self.url_open(
                        "/book/test-booking/confirm",
                        data={
                            "csrf_token": csrf,
                            "name": "Changed contact",
                            "email": partner.email,
                            "phone": "+1 555-9999",
                            "when": when,
                            "combination_id": str(self.rbcs[0].id),
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("error=", response.url)
                    partner.invalidate_recordset()
                    self.assertFalse(partner.phone)
                    self.assertEqual(
                        self.env["resource.booking"].search_count(
                            [("type_id", "=", self.rbt.id)]
                        ),
                        before,
                    )

    def test_confirm_invalid_date(self):
        """Submitting with invalid datetime redirects with error."""
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Test User",
            "email": "test@example.com",
            "phone": "+1 555-0103",
            "when": "not-a-date",
        }
        response = self.url_open("/book/test-booking/confirm", data=data)
        self.assertIn("error=", response.url)

    def test_confirm_honeypot_rejects_bot(self):
        """Submissions with the honeypot field filled are rejected."""
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Bot User",
            "email": "bot@example.com",
            "phone": "+1 555-9999",
            "when": "2021-03-01T10:00:00+00:00",
            "website": "spam-domain.com",
        }
        response = self.url_open("/book/test-booking/confirm", data=data)
        self.assertIn("error=", response.url)

    def test_confirm_rate_limit_blocks_rapid_submissions(self):
        """Two submissions within 5 seconds are rate-limited."""
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Fast User",
            "email": "fast@example.com",
            "phone": "+1 555-8888",
            "when": "2021-03-01T10:00:00+00:00",
        }
        # First submission succeeds
        response1 = self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        self.assertIn("/book/test-booking/success", response1.url)
        # Immediate second submission is blocked
        data["email"] = "fast2@example.com"
        response2 = self.url_open("/book/test-booking/confirm", data=data)
        self.assertIn("error=", response2.url)

    def test_success_page_renders(self):
        """Success page renders properly."""
        page = self._url_xml("/book/test-booking/success")
        self.assertTrue(page.cssselect("h1:contains('Booking Confirmed')"))

    def test_existing_partner_reused(self):
        """If partner with same email exists, it is reused."""
        self.env["res.partner"].create(
            {
                "name": "Existing User",
                "email": "existing@example.com",
                "phone": "+1 555-9999",
            }
        )
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Existing User",
            "email": "existing@example.com",
            "phone": "+1 555-0000",
            "when": "2021-03-01T10:00:00+00:00",
        }
        self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        partners = self.env["res.partner"].search(
            [("email", "=ilike", "existing@example.com")]
        )
        self.assertEqual(len(partners), 1)
        self.assertEqual(partners.phone, "+1 555-9999")

    def test_existing_partner_missing_phone_is_filled(self):
        """Submitting a phone fills an existing partner that has none."""
        self.env["res.partner"].create(
            {"name": "Phone Missing", "email": "missing-phone@example.com"}
        )
        page = self._url_xml("/book/test-booking/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Phone Missing",
            "email": "missing-phone@example.com",
            "phone": "+1 555-0104",
            "when": "2021-03-01T10:00:00+00:00",
        }
        self.url_open("/book/test-booking/confirm", data=data, timeout=30)
        partner = self.env["res.partner"].search(
            [("email", "=ilike", "missing-phone@example.com")], limit=1
        )
        self.assertEqual(partner.phone, "+1 555-0104")

    def test_website_description_displayed(self):
        """Website description is shown on the booking page."""
        self.rbt.website_description = "<p>Welcome to our booking page!</p>"
        page = self._url_xml("/book/test-booking")
        self.assertTrue(page.cssselect(":contains('Welcome to our booking page!')"))

    def test_location_pill_displayed(self):
        """Location is shown as a pill on the booking page."""
        self.rbt.location = "Main office"
        page = self._url_xml("/book/test-booking")
        self.assertTrue(page.cssselect(".o_wab_meta_pill:contains('Main office')"))


@freeze_time("2021-02-26 09:00:00", tick=True)
@tagged("post_install", "-at_install")
class TestBookingRaceCondition(HttpCase):
    """Test race condition handling with a single resource combination.

    Uses a separate class so that the combination limiting and pre-booking
    happen in ``setUpClass``, making them visible to the HTTP server thread.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.rbt.write(
            {
                "website_published": True,
                "website_slug": "race-test",
            }
        )
        # Limit to 1 combination so there is a real conflict
        cls.rbt.combination_rel_ids[1:].unlink()
        # Book the only slot via backend
        when_naive = datetime(2021, 3, 1, 10, 0)
        booking = cls.env["resource.booking"].create(
            {
                "type_id": cls.rbt.id,
                "partner_ids": [(4, cls.partner.id)],
                "combination_auto_assign": True,
            }
        )
        booking.start = when_naive
        booking.action_confirm()

    def _url_xml(self, url, data=None, timeout=10):
        """Open a URL and return its parsed lxml tree."""
        response = self.url_open(url, data, timeout=timeout)
        return fromstring(response.content)

    def _get_csrf_token(self, page):
        """Extract CSRF token from a page's hidden input."""
        inputs = page.cssselect('input[name="csrf_token"]')
        if inputs:
            return inputs[0].get("value")
        return ""

    def test_confirm_race_condition(self):
        """Double-booking the same slot shows an error message."""
        page = self._url_xml("/book/race-test/2021/3")
        csrf = self._get_csrf_token(page)
        data = {
            "csrf_token": csrf,
            "name": "Late Visitor",
            "email": "late@example.com",
            "phone": "+1 555-0105",
            "when": "2021-03-01T10:00:00+00:00",
        }
        response = self.url_open("/book/race-test/confirm", data=data, timeout=30)
        # Should redirect back to calendar with error, not to success
        self.assertNotIn("/success", response.url)
        self.assertIn("error=", response.url)
