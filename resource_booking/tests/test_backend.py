# Copyright 2021 Tecnativa - Jairo Llopis
# Copyright 2022 Tecnativa - Pedro M. Baeza
# Copyright 2024 Tecnativa - Carolina Fernandez
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from datetime import date, datetime, timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time
from pytz import timezone, utc

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, new_test_user, users
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon
from odoo.addons.resource.models.utils import Intervals
from odoo.addons.resource_booking.models.resource_booking import (
    _availability_is_fitting,
)

from .common import create_test_data

_2dt = fields.Datetime.to_datetime


class BackendCaseBase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_test_data(cls)
        cls.plain_user = new_test_user(cls.env, login="plain", groups="base.group_user")


@freeze_time("2021-02-26 09:00:00", tick=True)  # Last Friday of February
class BackendCaseMisc(BackendCaseBase):
    @users("plain")
    def test_plain_user_calendar_event_form(self):
        event_form = Form(
            self.env["calendar.event"].with_context(
                default_partner_ids=self.env.user.partner_id.ids,
                default_start="2023-01-01 00:00:00",
                default_stop="2023-01-01 01:00:00",
            )
        )
        event_form.name = "Test calendar event"
        event = event_form.save()
        self.assertEqual(event.name, "Test calendar event")

    @users("plain")
    @mute_logger("odoo.models.unlink")
    def test_plain_user_calendar_event(self):
        """Check that a simple user is able to handle manual calendar events."""
        event = self.env["calendar.event"].create(
            {
                "name": "Test calendar event",
                "start": "2023-01-01 00:00:00",
                "stop": "2023-01-01 01:00:00",
            }
        )
        event.write({"partner_ids": [(4, self.partner.id)]})
        event.unlink()

    def test_manager_rescheduling_keeps_resources_before_free_alternatives(self):
        manager = self.env.ref("base.user_admin")
        Booking = self.env["resource.booking"].with_user(manager)
        self.rbt.combination_assignment = "sorted"
        booking = Booking.create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [Command.link(self.partner.id)],
                "start": "2021-03-01 08:00:00",
            }
        )
        self.assertEqual(booking.combination_id, self.rbcs[0])
        # Tuesday has free alternatives, but a manager must keep Monday's resources.
        with Form(booking) as form:
            form.start = "2021-03-02 10:00:00"
            self.assertEqual(form.combination_id, self.rbcs[0])
        booking.write({"start": "2021-03-03 10:00:00", "duration": 2})
        self.assertEqual(booking.combination_id, self.rbcs[0])
        self.assertEqual(booking.meeting_id.stop, datetime(2021, 3, 3, 12))
        Booking.create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [Command.link(self.partner.id)],
                "start": "2021-03-01 09:00:00",
                "duration": 10,  # Explicit manager selection can exceed availability.
                "combination_auto_assign": False,
                "combination_id": self.rbcs[0].id,
            }
        )
        booking.meeting_id.write(
            {
                "start": "2021-03-01 09:00:00",
                "stop": "2021-03-01 12:00:00",
            }
        )
        self.assertEqual(booking.start, datetime(2021, 3, 1, 9))
        self.assertEqual(booking.duration, 3)
        self.assertEqual(booking.combination_id, self.rbcs[0])
        # Availability itself stays strict, even for the manager.
        self.assertFalse(
            self.rbcs[0]
            .resource_ids[0]
            .is_available(
                utc.localize(datetime(2021, 3, 1, 9)),
                utc.localize(datetime(2021, 3, 1, 10)),
            )
        )
        booking.combination_auto_assign = False
        booking.combination_auto_assign = True
        self.assertEqual(booking.combination_id, self.rbcs[0])
        other_type = self.rbt.copy(
            {
                "combination_rel_ids": [
                    Command.clear(),
                    Command.create(
                        {
                            "combination_id": self.rbcs[2].id,
                        }
                    ),
                ],
            }
        )
        booking.type_id = other_type
        self.assertEqual(booking.combination_id, self.rbcs[2])
        empty = self.env["resource.booking.combination"].create({"resource_ids": []})
        for values in (
            {"combination_auto_assign": False, "combination_id": empty.id},
            {},  # No arbitrary fallback for a new automatic booking on Wednesday.
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(ValidationError),
                self.env.cr.savepoint(),
            ):
                Booking.create(
                    {
                        "type_id": self.rbt.id,
                        "partner_ids": [Command.link(self.partner.id)],
                        "start": "2021-03-03 10:00:00",
                        **values,
                    }
                )

    def test_rescheduling_override_requires_actual_internal_manager(self):
        user = new_test_user(
            self.env,
            login="booking_user",
            groups="base.group_user,resource_booking.group_user",
        )
        booking = self.env["resource.booking"].create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [Command.link(user.partner_id.id)],
                "start": "2021-03-01 08:00:00",
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
            }
        )
        actors = (
            booking.with_user(user),
            booking.with_user(user).sudo(),
            booking.with_user(self.env.ref("base.user_admin")).with_context(
                using_portal=True
            ),
        )
        for actor in actors:
            for record, values in (
                (actor, {"start": "2021-03-02 08:00:00"}),
                (actor, {"duration": 10}),
                (
                    actor.meeting_id,
                    {"start": "2021-03-02 08:00:00", "stop": "2021-03-02 09:00:00"},
                ),
            ):
                with (
                    self.subTest(uid=actor.env.uid, su=actor.env.su, values=values),
                    self.assertRaises(ValidationError),
                    self.env.cr.savepoint(),
                ):
                    record.write(values)
        self.assertEqual(booking.start, datetime(2021, 3, 1, 8))
        self.assertEqual(booking.combination_id, self.rbcs[0])

    def test_modification_deadline_uses_original_schedule_not_initial_booking(self):
        self.rbt.resource_calendar_id = self.r_calendars[3]
        booking = (
            self.env["resource.booking"]
            .with_context(using_portal=True)
            .create(
                {
                    "type_id": self.rbt.id,
                    "partner_ids": [Command.link(self.partner.id)],
                    "combination_id": self.rbcs[3].id,
                    "combination_auto_assign": False,
                }
            )
        )
        # First-time scheduling inside the modification deadline remains valid.
        booking.start = "2021-02-26 12:00:00"
        booking.write({"name": "Metadata", "start": booking.start})
        booking.meeting_id.write({"name": "Metadata", "start": booking.start})
        booking.action_confirm()
        for record, values in (
            (booking, {"start": "2021-03-05 12:00:00"}),
            (booking, {"duration": 2}),
            (
                booking.meeting_id,
                {"start": "2021-03-05 12:00:00", "stop": "2021-03-05 13:00:00"},
            ),
            (booking.meeting_id, {"stop": "2021-02-26 14:00:00"}),
        ):
            with (
                self.subTest(values=values),
                self.assertRaises(ValidationError),
                self.env.cr.savepoint(),
            ):
                record.write(values)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            booking.meeting_id.unlink()
        self.assertEqual(booking.start, datetime(2021, 2, 26, 12))
        booking.with_context(using_portal=False).start = "2021-02-28 12:00:00"
        # The OLD deadline allows moving into short notice, not the other way around.
        booking.start = "2021-02-26 13:00:00"
        self.assertEqual(booking.meeting_id.start, datetime(2021, 2, 26, 13))

    def test_mixed_type_reschedule_uses_original_modification_deadline(self):
        user = new_test_user(
            self.env,
            login="deadline_user",
            groups="base.group_user,resource_booking.group_user",
        )
        self.rbt.write(
            {
                "resource_calendar_id": self.r_calendars[3].id,
                "modifications_deadline": 0,
            }
        )
        stricter_type = self.rbt.copy({"modifications_deadline": 24})
        original_start = datetime(2021, 2, 26, 21)  # Twelve hours from now.
        future_start = original_start + timedelta(days=7)
        booking = self.env["resource.booking"].with_user(user).create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [Command.link(user.partner_id.id)],
                "combination_id": self.rbcs[3].id,
                "combination_auto_assign": False,
                "start": original_start,
                "duration": 1,
            }
        )
        event = booking.meeting_id
        booking_id = booking.id
        self.assertFalse(booking.env.su)
        self.assertFalse(booking._can_override_scheduling())
        booking.write({"type_id": stricter_type.id, "start": future_start})
        self.assertEqual(booking.id, booking_id)
        self.assertEqual(booking.meeting_id, event)
        self.assertEqual(booking.type_id, stricter_type)
        self.assertEqual(booking.start, future_start)
        self.assertEqual(event.start, future_start)
        # Moving back into short notice is allowed by the still-open old deadline.
        booking.write({"start": original_start})
        forged_context = {
            "_booking_deadline_token": True,
            "_booking_deadline_event_ids": event.ids,
            "syncing_booking_ids": booking.ids,
        }
        for context in ({}, forged_context):
            for record, values in (
                (booking, {"type_id": self.rbt.id, "start": future_start}),
                (
                    event,
                    {"start": future_start, "stop": future_start + timedelta(hours=1)},
                ),
            ):
                with (
                    self.subTest(context=context, model=record._name),
                    self.assertRaises(ValidationError),
                    self.env.cr.savepoint(),
                ):
                    record.with_context(**context).write(values)
            with self.assertRaises(ValidationError), self.env.cr.savepoint():
                event.with_context(**context).unlink()
        self.assertEqual(booking.type_id, stricter_type)
        self.assertEqual(booking.start, original_start)
        self.assertEqual(booking.meeting_id, event)

    @freeze_time("2021-02-26 09:00:00")
    def test_submitted_slot_validation_reuses_strict_slots(self):
        self.rbt.write({"min_advance_booking_hours": 72, "max_advance_booking_days": 4})
        booking = self.env["resource.booking"].create(
            {
                "type_id": self.rbt.id,
                "partner_ids": [Command.link(self.partner.id)],
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
                "duration": 1.5,
                "start": "2021-03-01 10:00:00",
            }
        )
        # Excludes the booking's own meeting and normalizes offset-aware input to UTC.
        self.assertEqual(
            booking._validate_submitted_slot("2021-03-01T12:00:00+02:00"),
            datetime(2021, 3, 1, 10),
        )
        for when in (
            "bad-date",
            "2021-03-01T10:00:00",
            "2021-02-22T10:00:00Z",
            "2021-03-01T08:00:00Z",
            "2021-03-02T10:00:00Z",
            "2021-03-01T10:15:00Z",
            "2021-03-01T16:00:00Z",
        ):
            with self.subTest(when=when), self.assertRaises(ValidationError):
                booking._validate_submitted_slot(when)
        self.assertEqual(booking.start, datetime(2021, 3, 1, 10))
        self.rbt.max_advance_booking_days = 0
        self.assertEqual(
            booking._validate_submitted_slot("2021-03-22T10:00:00Z"),
            datetime(2021, 3, 22, 10),
        )
        # A displayed union slot must still fit one full resource combination.
        morning = self.r_calendars[0].copy()
        morning.attendance_ids.hour_to = 12
        afternoon = self.r_calendars[0].copy()
        afternoon.attendance_ids.hour_from = 12
        self.rbcs[0].forced_calendar_id = morning
        self.rbcs[2].forced_calendar_id = afternoon
        phantom = self.env["resource.booking"].new(
            {
                "type_id": self.rbt.id,
                "duration": 2,
            }
        )
        slots = phantom.with_context(using_portal=True, tz="UTC")._get_calendar_context(
            2021, 3
        )["slots"]
        self.assertIn(utc.localize(datetime(2021, 3, 8, 11)), slots[date(2021, 3, 8)])
        with self.assertRaisesRegex(
            ValidationError, "No resource combinations available"
        ):
            phantom._validate_submitted_slot("2021-03-08T11:00:00Z")

    def test_submitted_slot_validation_preserves_multiday_grid(self):
        self.rbt.write(
            {
                "resource_calendar_id": self.r_calendars[3].id,
                "slot_duration": 7,
                "min_advance_booking_hours": 0,
                "max_advance_booking_days": 0,
            }
        )
        phantom = (
            self.env["resource.booking"]
            .with_context(using_portal=True, tz="UTC")
            .new(
                {
                    "type_id": self.rbt.id,
                    "duration": 1,
                    "combination_id": self.rbcs[3].id,
                    "combination_auto_assign": False,
                }
            )
        )
        slots = phantom._get_calendar_context(2021, 3)["slots"]
        # Friday's continuous interval anchors Saturday at 04:00, not midnight.
        self.assertIn(utc.localize(datetime(2021, 3, 6, 4)), slots[date(2021, 3, 6)])
        for day in (date(2021, 3, 6), date(2021, 3, 7)):
            for start in slots[day]:
                with self.subTest(start=start):
                    self.assertEqual(
                        phantom._validate_submitted_slot(start.isoformat()),
                        start.replace(tzinfo=None),
                    )
        midnight = utc.localize(datetime(2021, 3, 6))
        self.assertNotIn(midnight, slots[midnight.date()])
        with self.assertRaises(ValidationError):
            phantom._validate_submitted_slot(midnight.isoformat())

    @freeze_time("2021-04-30 00:00:00")
    def test_submitted_slot_validation_preserves_display_timezone_grid(self):
        calendar = self.r_calendars[3].copy(
            {
                "attendance_ids": [
                    Command.create(
                        {
                            "name": "All day",
                            "dayofweek": str(day),
                            "hour_from": 0,
                            "hour_to": 24,
                            "day_period": "morning",
                        }
                    )
                    for day in range(7)
                ],
            }
        )
        self.rbt.write(
            {
                "resource_calendar_id": calendar.id,
                "slot_duration": 7,
                "min_advance_booking_hours": 0,
                "max_advance_booking_days": 0,
            }
        )
        self.rbcs[3].forced_calendar_id = calendar
        values = {
            "type_id": self.rbt.id,
            "duration": 1,
            "combination_id": self.rbcs[3].id,
            "combination_auto_assign": False,
        }
        for tz, hour in (("UTC", 0), ("Asia/Tokyo", 14)):
            with self.subTest(tz=tz):
                # A subtest clears ORM caches, including non-persistent records.
                booking = self.env["resource.booking"].with_context(
                    using_portal=True, tz=tz
                ).new(values)
                display_tz = timezone(tz)
                slots = booking._get_calendar_context(2021, 5)["slots"]
                starts = [start for values in slots.values() for start in values]
                # The selected month's boundary anchors the continuous 7-hour grid.
                self.assertIn(display_tz.localize(datetime(2021, 5, 1, hour)), starts)
                for start in starts:
                    local = start.astimezone(display_tz)
                    if local.date() == date(2021, 5, 1):
                        self.assertEqual(
                            booking._validate_submitted_slot(local.isoformat()),
                            start.astimezone(utc).replace(tzinfo=None),
                        )
                midnight = display_tz.localize(datetime(2021, 5, 2))
                self.assertNotIn(midnight, starts)
                with self.assertRaises(ValidationError):
                    booking._validate_submitted_slot(midnight.isoformat())

    def test_scheduling_conflict_constraints(self):
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        # Combination is available on Mondays and Tuesdays
        rbc_montue = self.rbcs[2]
        # Type is available on Mondays
        cal_mon = self.r_calendars[0]
        self.rbt.resource_calendar_id = cal_mon
        # Booking cannot be placed next Tuesday
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-02 08:00:00",
                    "type_id": self.rbt.id,
                    "combination_id": rbc_montue.id,
                    "combination_auto_assign": False,
                }
            )
        # Booking cannot be placed next Monday before 8:00
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-02 07:45:00",
                    "type_id": self.rbt.id,
                    "combination_id": rbc_montue.id,
                    "combination_auto_assign": False,
                }
            )
        # Booking cannot be placed next Monday after 17:00
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-02 16:45:00",
                    "type_id": self.rbt.id,
                    "combination_id": rbc_montue.id,
                    "combination_auto_assign": False,
                }
            )
        # Booking can be placed next Monday
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": rbc_montue.id,
                "combination_auto_assign": False,
            }
        )
        # Another event cannot collide with the same RBC
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-01 08:29:59",
                    "type_id": self.rbt.id,
                    "combination_id": rbc_montue.id,
                    "combination_auto_assign": False,
                }
            )
        # Another event can collide with another RBC
        rbc_mon = self.rbcs[0]
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": rbc_mon.id,
                "combination_auto_assign": False,
            }
        )

    def test_scheduling_constraints_span_two_days(self):
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        # Booking can span across two calendar days.
        cal_frisun = self.r_calendars[3]
        rbc_frisun = self.rbcs[3]
        self.rbt.resource_calendar_id = cal_frisun
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-06 23:00:00",
                "duration": 2,
                "type_id": self.rbt.id,
                "combination_id": rbc_frisun.id,
                "combination_auto_assign": False,
            }
        )
        # Booking cannot overlap.
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-06 22:00:00",
                    "duration": 4,
                    "type_id": self.rbt.id,
                    "combination_id": rbc_frisun.id,
                    "combination_auto_assign": False,
                }
            )
        # Test a case where there is an overlap, but the conflict happens at
        # 00:00 exactly.
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-14 00:00:00",
                "duration": 1,
                "type_id": self.rbt.id,
                "combination_id": rbc_frisun.id,
                "combination_auto_assign": False,
            }
        )
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-13 23:00:00",
                    "duration": 4,
                    "type_id": self.rbt.id,
                    "combination_id": rbc_frisun.id,
                    "combination_auto_assign": False,
                }
            )
        # If there are too many minutes between the end and start of the two
        # dates, the booking cannot be contiguous.
        cal_frisun.attendance_ids.write({"hour_to": 23.96})  # 23:58
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-20 23:00:00",
                    "duration": 2,
                    "type_id": self.rbt.id,
                    "combination_id": rbc_frisun.id,
                    "combination_auto_assign": False,
                }
            )

    def test_scheduling_constraints_span_three_days(self):
        # Booking can span across two calendar days.
        cal_frisun = self.r_calendars[3]
        rbc_frisun = self.rbcs[3]
        self.rbt.resource_calendar_id = cal_frisun
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-05 23:00:00",
                "duration": 24 * 2,
                "type_id": self.rbt.id,
                "combination_id": rbc_frisun.id,
                "combination_auto_assign": False,
            }
        )

    def test_availability_is_fitting_malformed_date_skip(self):
        """Test a case for malformed data where a date is skipped in the
        available_intervals list of tuples.
        """
        recset = self.env["resource.booking"]
        tuples = [
            (
                datetime(2021, 3, 1, 18, 0),
                datetime(2021, 3, 1, 23, 59, 59, 999999),
                recset,
            ),
            (
                datetime(2021, 3, 2, 0, 0),
                datetime(2021, 3, 2, 23, 59, 59, 999999),
                recset,
            ),
            (datetime(2021, 3, 3, 0, 0), datetime(2021, 3, 3, 18, 0), recset),
        ]
        available_intervals = Intervals(tuples)
        self.assertTrue(
            _availability_is_fitting(
                available_intervals,
                datetime(2021, 3, 1, 18, 0),
                datetime(2021, 3, 3, 18, 0),
            )
        )
        # Skip a day by removing it.
        tuples.pop(1)
        available_intervals = Intervals(tuples)
        self.assertFalse(
            _availability_is_fitting(
                available_intervals,
                datetime(2021, 3, 1, 18, 0),
                datetime(2021, 3, 3, 18, 0),
            )
        )

    def test_rbc_forced_calendar(self):
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        # Type is available on Mondays
        cal_mon = self.r_calendars[0]
        self.rbt.resource_calendar_id = cal_mon
        # Cannot book an combination with resources that only work on Tuesdays
        rbc_tue = self.rbcs[1]
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["resource.booking"].create(
                {
                    "partner_ids": [(4, self.partner.id)],
                    "start": "2021-03-01 08:00:00",
                    "type_id": self.rbt.id,
                    "combination_id": rbc_tue.id,
                    "combination_auto_assign": False,
                }
            )
        # However, if the combination is forced to Mondays, you can book it
        rbc_tue.forced_calendar_id = cal_mon
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_auto_assign": False,
                "combination_id": rbc_tue.id,
            }
        )
        self.assertEqual(rb.combination_id, rbc_tue)

    def test_booking_from_calendar_view(self):
        # The type is configured by default with bookings of 30 minutes
        self.assertEqual(self.rbt.duration, 0.5)
        # Change it to 45 minutes
        self.rbt.duration = 0.75
        # Bookings smart button configures calendar with slots from slot duration field
        button_context = self.rbt.action_open_bookings()["context"]
        self.assertEqual(button_context["calendar_slot_duration"], "00:30:00")
        self.assertEqual(button_context["default_duration"], 0.75)
        # When you click & drag on calendar to create an event, it adds the
        # start and duration as default; we imitate that here to book a meeting
        # with 2 slots next monday
        button_context["default_duration"] = 1.5
        booking_form = Form(
            self.env["resource.booking"].with_context(
                **button_context,
                default_start="2021-03-01 08:00:00",
            )
        )
        # This might seem redundant, but makes sure onchanges don't mess stuff
        self.assertEqual(_2dt(booking_form.start), datetime(2021, 3, 1, 8))
        self.assertEqual(booking_form.duration, 1.5)
        self.assertEqual(_2dt(booking_form.stop), datetime(2021, 3, 1, 9, 30))
        # If I change to next week's monday, then the stop date advances 1:30h
        booking_form.start = datetime(2021, 3, 8, 8)
        booking_form.partner_ids.add(self.partner)
        self.assertEqual(_2dt(booking_form.start), datetime(2021, 3, 8, 8))
        self.assertEqual(booking_form.duration, 1.5)
        self.assertEqual(_2dt(booking_form.stop), datetime(2021, 3, 8, 9, 30))
        # I can book it (which means type & combination were autofilled)
        booking = booking_form.save()
        self.assertTrue(booking.meeting_id)
        self.assertEqual(booking.state, "scheduled")

    def test_meeting_description_combines_booking_description_and_requester_advice(
        self,
    ):
        """Portal description should be combined with the type's default requester advice."""
        self.rbt.requester_advice = "Please bring your ID."
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
                "description": "<p>I need help with taxes.</p>",
            }
        )
        meeting_vals = booking._prepare_meeting_vals()
        self.assertIn("I need help with taxes.", meeting_vals["description"])
        self.assertIn("Please bring your ID.", meeting_vals["description"])

    def test_meeting_description_fallback_to_booking_description(self):
        """If no requester advice, the booking description alone should be used."""
        self.rbt.requester_advice = False
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
                "description": "<p>Only this.</p>",
            }
        )
        meeting_vals = booking._prepare_meeting_vals()
        self.assertEqual(meeting_vals["description"], "<p>Only this.</p>")

    def test_meeting_description_fallback_to_requester_advice(self):
        """If no booking description, the requester advice alone should be used."""
        self.rbt.requester_advice = "Default advice."
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
                "description": False,
            }
        )
        meeting_vals = booking._prepare_meeting_vals()
        self.assertEqual(meeting_vals["description"], "<p>Default advice.</p>")

    def test_meeting_includes_type_default_tags(self):
        """Default tags on the booking type must reach the calendar event."""
        tag_a = self.env["calendar.event.type"].create({"name": "Type default tag"})
        tag_b = self.env["calendar.event.type"].create({"name": "Booking tag"})
        self.rbt.categ_ids = tag_a
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
                "categ_ids": [(4, tag_b.id)],
            }
        )
        meeting_vals = booking._prepare_meeting_vals()
        self.assertIn(tag_a.id, meeting_vals["categ_ids"][0][2])
        self.assertIn(tag_b.id, meeting_vals["categ_ids"][0][2])

    def test_meeting_includes_type_default_tags_when_booking_has_none(self):
        """Default tags reach the event even when the booking was created without them."""
        tag = self.env["calendar.event.type"].create({"name": "Type default tag"})
        self.rbt.categ_ids = tag
        booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
            }
        )
        self.assertIn(tag, booking.meeting_id.categ_ids)

    def test_available_slots_respect_max_advance_booking_days(self):
        self.rbt.write(
            {
                "max_advance_booking_days": 4,
                "min_advance_booking_hours": 0,
                "resource_calendar_id": self.r_calendars[2].id,
                "slot_duration": 0.5,
            }
        )
        booking = self.env["resource.booking"].new(
            {"type_id": self.rbt.id, "duration": self.rbt.duration}
        )
        now = fields.Datetime.context_timestamp(booking, fields.Datetime.now())
        slots = booking._get_available_slots(now, now + timedelta(days=10))
        max_start = now + timedelta(days=4)
        self.assertTrue(slots)
        self.assertTrue(
            all(slot <= max_start for day_slots in slots.values() for slot in day_slots)
        )
        self.assertFalse(any(day > max_start.date() for day in slots))

    def test_min_advance_booking_hours_and_modifications_deadline_are_independent(self):
        """Minimum advance booking controls slot availability;
        modification deadline controls overdue status. They must not be coupled."""
        # Use FriSun calendar so today (frozen Friday) is a valid booking day
        self.rbt.write(
            {
                "min_advance_booking_hours": 2,
                "modifications_deadline": 24,
                "resource_calendar_id": self.r_calendars[3].id,
                "slot_duration": 0.5,
            }
        )
        now = fields.Datetime.now()
        # A slot 1 hour from now should be blocked by min_advance_booking_hours
        booking = self.env["resource.booking"].new(
            {"type_id": self.rbt.id, "duration": self.rbt.duration}
        )
        tz_now = fields.Datetime.context_timestamp(booking, now)
        slots = booking._get_available_slots(tz_now, tz_now + timedelta(days=1))
        self.assertTrue(slots, "Expected slots to be found today or tomorrow")
        for day_slots in slots.values():
            for slot in day_slots:
                self.assertGreaterEqual(
                    slot,
                    tz_now + timedelta(hours=2),
                    "Slots should respect min_advance_booking_hours",
                )
        # A booking 3 hours from now IS overdue (deadline is 24h before start)
        future_start = now + timedelta(hours=3)
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": future_start,
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[3].id,
                "combination_auto_assign": False,
            }
        )
        self.assertTrue(
            rb.is_overdue,
            "Booking should be overdue when start is within modifications_deadline",
        )
        # A booking 25 hours from now is NOT overdue (deadline is 24h before start)
        rb2 = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": now + timedelta(hours=25),
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[3].id,
                "combination_auto_assign": False,
            }
        )
        self.assertFalse(
            rb2.is_overdue,
            "Booking should not be overdue when modifications_deadline is 24h and start is 25h away",
        )

    def test_resource_buffer_blocks_shared_resource_following_slots(self):
        self.rbt.write(
            {
                "combination_assignment": "sorted",
                "duration": 1.0,
                "min_advance_booking_hours": 0,
                "resource_calendar_id": self.r_calendars[0].id,
                "slot_duration": 0.25,
            }
        )
        self.r_users[0].booking_buffer = 0.5
        shared_material = self.env["resource.resource"].create(
            {
                "name": "Shared-resource alternate material",
                "calendar_id": self.r_calendars[0].id,
                "resource_type": "material",
                "tz": "UTC",
            }
        )
        shared_user_combination = self.env["resource.booking.combination"].create(
            {"resource_ids": [Command.set([self.r_users[0].id, shared_material.id])]}
        )
        other_type = self.env["resource.booking.type"].create(
            {
                "name": "Other booking option",
                "combination_rel_ids": [
                    Command.create(
                        {"sequence": 0, "combination_id": shared_user_combination.id}
                    )
                ],
                "duration": 1.0,
                "min_advance_booking_hours": 0,
                "resource_calendar_id": self.r_calendars[0].id,
                "slot_duration": 0.25,
            }
        )
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "duration": 1.0,
                "type_id": self.rbt.id,
                "combination_id": self.rbcs[0].id,
                "combination_auto_assign": False,
            }
        )
        booking = self.env["resource.booking"].new(
            {
                "type_id": other_type.id,
                "duration": 1.0,
                "combination_id": shared_user_combination.id,
                "combination_auto_assign": False,
            }
        )
        slots = booking._get_available_slots(
            utc.localize(datetime(2021, 3, 1)),
            utc.localize(datetime(2021, 3, 2)),
        )
        monday_slots = slots[datetime(2021, 3, 1).date()]
        self.assertNotIn(utc.localize(datetime(2021, 3, 1, 9)), monday_slots)
        self.assertIn(utc.localize(datetime(2021, 3, 1, 9, 30)), monday_slots)

        unrelated_booking = self.env["resource.booking"].new(
            {
                "type_id": self.rbt.id,
                "duration": 1.0,
                "combination_id": self.rbcs[2].id,
                "combination_auto_assign": False,
            }
        )
        unrelated_slots = unrelated_booking._get_available_slots(
            utc.localize(datetime(2021, 3, 1)),
            utc.localize(datetime(2021, 3, 2)),
        )
        self.assertIn(
            utc.localize(datetime(2021, 3, 1, 9)),
            unrelated_slots[datetime(2021, 3, 1).date()],
        )

    def test_dates_inverse(self):
        """Start & stop fields are computed with inverse. Test their workflow."""
        # Set type to be available only on mondays
        self.rbt.resource_calendar_id = self.r_calendars[0]
        # Create a booking from scratch
        booking_form = Form(self.env["resource.booking"])
        booking_form.type_id = self.rbt
        booking_form.partner_ids.add(self.partner)
        self.assertFalse(booking_form.start)
        self.assertFalse(booking_form.stop)
        self.assertFalse(booking_form.combination_id)
        # I can save it without booking
        booking = booking_form.save()
        self.assertEqual(booking.state, "pending")
        self.assertFalse(booking.meeting_id)
        self.assertFalse(booking.start)
        self.assertFalse(booking.stop)
        self.assertFalse(booking.combination_id)
        # I edit it again
        with Form(booking) as booking_form:
            # Start next Tuesday: updates stop; no combination available
            booking_form.start = datetime(2021, 3, 2, 8)
            self.assertEqual(_2dt(booking_form.stop), datetime(2021, 3, 2, 8, 30))
            self.assertFalse(booking_form.combination_id)
            # Move to Monday: updates stop; found one combination available
            booking_form.start = datetime(2021, 3, 1, 8)
            self.assertEqual(_2dt(booking_form.stop), datetime(2021, 3, 1, 8, 30))
            self.assertTrue(booking_form.combination_id)
        self.assertEqual(booking.state, "scheduled")
        self.assertTrue(booking.meeting_id)
        self.assertTrue(booking.start)
        self.assertTrue(booking.stop)
        self.assertTrue(booking.combination_id)

    @mute_logger("odoo.models.unlink")
    def test_state(self):
        # I create a pending booking
        booking = self.env["resource.booking"].create(
            {"type_id": self.rbt.id, "partner_ids": [(4, self.partner.id)]}
        )
        # Without dates, it's pending
        self.assertEqual(booking.state, "pending")
        self.assertTrue(booking.active)
        self.assertFalse(booking.meeting_id)
        self.assertFalse(booking.start)
        self.assertFalse(booking.stop)
        self.assertFalse(booking.combination_id)
        # With a linked meeting, it's scheduled
        with Form(booking) as booking_form:
            booking_form.start = datetime(2021, 3, 1, 8)
        meeting = booking.meeting_id
        self.assertEqual(booking.state, "scheduled")
        self.assertTrue(booking.active)
        self.assertTrue(meeting.exists())
        self.assertTrue(booking.start)
        self.assertTrue(booking.stop)
        self.assertTrue(booking.combination_id)
        # When partner confirms attendance, it's confirmed
        booker_attendance = meeting.attendee_ids.filtered(
            lambda one: one.partner_id in booking.partner_ids
        )
        self.assertTrue(booker_attendance)
        booker_attendance.do_accept()
        self.assertEqual(booking.state, "confirmed")
        self.assertTrue(booking.active)
        self.assertTrue(meeting.exists())
        self.assertTrue(booking.start)
        self.assertTrue(booking.stop)
        self.assertTrue(booking.combination_id)
        # Without dates, it's pending again
        booking.action_unschedule()
        self.assertEqual(booking.state, "pending")
        self.assertTrue(booking.active)
        self.assertFalse(meeting.exists())
        self.assertFalse(booking.start)
        self.assertFalse(booking.stop)
        self.assertTrue(booking.combination_id)
        # Archived and without dates, it's canceled
        booking.action_cancel()
        self.assertEqual(booking.state, "canceled")
        self.assertFalse(booking.active)
        self.assertFalse(meeting.exists())
        self.assertFalse(booking.start)
        self.assertFalse(booking.stop)
        self.assertTrue(booking.combination_id)

    def test_sorted_assignment(self):
        """Set sorted assignment on RBT and test it works correctly."""
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        rbc_mon, rbc_tue, rbc_montue, rbc_frisun = self.rbcs
        with Form(self.rbt) as rbt_form:
            rbt_form.combination_assignment = "sorted"
        # Book next monday at 10:00
        rb1_form = Form(self.env["resource.booking"])
        rb1_form.type_id = self.rbt
        rb1_form.partner_ids.add(self.partner)
        rb1_form.start = datetime(2021, 3, 1, 10)
        self.assertEqual(rb1_form.combination_id, rbc_mon)
        rb1 = rb1_form.save()
        self.assertEqual(rb1.combination_id, rbc_mon)
        # Another booking, same time
        rb2_form = Form(self.env["resource.booking"])
        rb2_form.type_id = self.rbt
        rb2_form.partner_ids.add(self.partner)
        rb2_form.start = datetime(2021, 3, 1, 10)
        self.assertEqual(rb2_form.combination_id, rbc_montue)
        rb2 = rb2_form.save()
        self.assertEqual(rb2.combination_id, rbc_montue)
        # I'm able to alter rb1 timing
        with Form(rb1) as rb1_form:
            rb1_form.start = datetime(2021, 3, 2, 10)
            self.assertEqual(rb1_form.combination_id, rbc_tue)
        self.assertEqual(rb1.combination_id, rbc_tue)

    def test_calendar_meeting_and_leave_combined(self):
        """Resource not bookable on calendar leave."""
        cal_mon = self.r_calendars[0]
        res_mon = self.r_users[0]
        # Add leave next Monday for Mon resource
        self.env["resource.calendar.leaves"].create(
            {
                "date_from": datetime(2021, 3, 1),
                "date_to": datetime(2021, 3, 3),
                "calendar_id": cal_mon.id,
                "resource_id": res_mon.id,
            }
        )
        # Add meeting same day for all resources, so no combination is available
        self.env["calendar.event"].create(
            {
                "start": datetime(2021, 3, 1, 8),
                "stop": datetime(2021, 3, 1, 10, 30),
                "name": "some meeting",
                "partner_ids": [Command.set(self.users.partner_id.ids)],
            }
        )
        # Check it's not bookable
        rb_form = Form(self.env["resource.booking"])
        rb_form.type_id = self.rbt
        rb_form.partner_ids.add(self.partner)
        # No combination found
        rb_form.start = datetime(2021, 3, 1, 10)
        self.assertFalse(rb_form.combination_id)
        # Combination found
        rb_form.start = datetime(2021, 3, 8, 10)
        self.assertTrue(rb_form.combination_id)
        rb_form.save()

    def test_same_slot_twice_not_utc(self):
        """Scheduling the same slot twice fails, when not in UTC."""
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        for loop in range(2):
            rb_f = Form(self.env["resource.booking"].with_context(tz="Europe/Madrid"))
            rb_f.partner_ids.add(self.partner)
            rb_f.type_id = self.rbt
            rb_f.start = datetime(2021, 3, 1, 10)
            rb_f.combination_auto_assign = False
            rb_f.combination_id = self.rbcs[0]
            # 1st one works
            if loop == 0:
                rb = rb_f.save()
                self.assertEqual(rb.state, "scheduled")
            else:
                with self.assertRaises(ValidationError):
                    rb_f.save()

    def test_recurring_event(self):
        """Recurrent events are considered."""
        # Everyone busy past and next Mondays with a recurring meeting
        ce_f = Form(self.env["calendar.event"].with_context(default_mon=True))
        ce_f.name = "recurring event past monday"
        for user in self.users:
            ce_f.partner_ids.add(user.partner_id)
        ce_f.start = datetime(2021, 2, 22, 8)
        ce_f.duration = 1
        ce_f.recurrency = True
        ce_f.rrule_type_ui = "weekly"
        ce_f.end_type = "count"
        ce_f.count = 2
        ce_f.save()
        # Cannot book next Monday at 8
        rb_f = Form(self.env["resource.booking"])
        rb_f.partner_ids.add(self.partner)
        rb_f.type_id = self.rbt
        # No RBC when starting
        self.assertFalse(rb_f.combination_id)
        # No RBC available next Monday at 8
        rb_f.start = datetime(2021, 3, 1, 8)
        self.assertFalse(rb_f.combination_id)
        # Everyone's free at 9
        rb_f.start = datetime(2021, 3, 1, 9)
        self.assertTrue(rb_f.combination_id)

    def test_allday_event_blocks_booking_slot(self):
        """All-day calendar events block booking slots that overlap their day.

        Without all-day handling, the date-only event is invisible to the
        scheduling search (which queries on start/stop datetimes), so the
        booking is incorrectly accepted.
        """
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        user = self.users[0]
        self.env["calendar.event"].create(
            {
                "name": "PTO",
                "allday": True,
                "start_date": "2021-03-01",
                "stop_date": "2021-03-01",
                "user_id": user.id,
                "partner_ids": [Command.set([user.partner_id.id])],
            }
        )
        rb_f = Form(self.env["resource.booking"])
        rb_f.partner_ids.add(self.partner)
        rb_f.type_id = self.rbt
        # Force the user-resource combination so the all-day event has to block it
        rb_f.combination_auto_assign = False
        rb_f.combination_id = self.rbcs[0]
        rb_f.start = datetime(2021, 3, 1, 9)
        with self.assertRaises(ValidationError):
            rb_f.save()
        # Following Monday is fine
        rb_f.start = datetime(2021, 3, 8, 9)
        rb_f.save()

    @mute_logger("odoo.models.unlink")
    def test_change_calendar_after_bookings_exist(self):
        """Calendar changes are allowed even when confirmed bookings exist,
        but new or rescheduled bookings must still respect the updated calendar.
        """
        self.env = self.env(context=dict(self.env.context, using_portal=True))
        rbc_mon = self.rbcs[0]
        cal_mon = self.r_calendars[0]
        # There's a booking for last monday
        past_booking = self.env["resource.booking"].create(
            {
                "combination_id": rbc_mon.id,
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-02-22 08:00:00",
                "type_id": self.rbt.id,
            }
        )
        past_booking.action_confirm()
        self.assertEqual(past_booking.duration, 0.5)
        self.assertEqual(past_booking.state, "confirmed")
        # There's another one for next monday, confirmed too
        future_booking = self.env["resource.booking"].create(
            {
                "combination_id": rbc_mon.id,
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_auto_assign": False,
            }
        )
        future_booking.action_confirm()
        self.assertEqual(future_booking.state, "confirmed")
        # Calendar can be changed even though a confirmed future booking exists
        with Form(cal_mon) as cal_mon_f:
            with cal_mon_f.attendance_ids.edit(0) as att_mon_f:
                att_mon_f.hour_from = 9
        # Unschedule future booking
        future_booking.action_unschedule()
        # Cannot reschedule to the old slot because calendar now starts at 9
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            with Form(future_booking) as future_booking_f:
                future_booking_f.start = "2021-03-01 08:00:00"
        # Can reschedule to the new valid slot
        with Form(future_booking) as future_booking_f:
            future_booking_f.start = "2021-03-01 09:00:00"
        self.assertEqual(future_booking.state, "scheduled")
        # Confirming the rescheduled booking succeeds
        future_booking.action_confirm()
        self.assertEqual(future_booking.state, "confirmed")

    def test_free_slots_with_different_type_and_booking_durations(self):
        """Slot and booking duration are different, and all works."""
        # Type and calendar allow one slot each 30 minutes on Mondays and
        # Tuesdays from 08:00 to 17:00 UTC. The booking will span for 3 slots.
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "duration": self.rbt.duration * 3,
            }
        )
        self.assertEqual(rb.duration, 1.5)
        slots = rb._get_available_slots(
            utc.localize(datetime(2021, 3, 2, 14, 15)),
            utc.localize(datetime(2021, 3, 8, 10)),
        )
        self.assertEqual(
            slots,
            {
                # Thursday
                date(2021, 3, 2): [
                    # We start searching at 14:15, so first free slot will
                    # start at 14:30
                    utc.localize(datetime(2021, 3, 2, 14, 30)),
                    utc.localize(datetime(2021, 3, 2, 15)),
                    # Booking duration is 1:30, and calendar ends at 17:00, so
                    # last slot starts at 15:30
                    utc.localize(datetime(2021, 3, 2, 15, 30)),
                ],
                # Next Monday, because calendar only allows Mondays and Tuesdays
                date(2021, 3, 8): [
                    # Calendar starts at 8:00
                    utc.localize(datetime(2021, 3, 8, 8)),
                    # We are searching until 10:00, so last free slot is at 8:30
                    utc.localize(datetime(2021, 3, 8, 8, 30)),
                ],
            },
        )

    @mute_logger("odoo.models.unlink")
    def test_location(self):
        """Location across records works as expected."""
        rbt2 = self.rbt.copy({"location": "Office 2"})
        rb_f = Form(self.env["resource.booking"])
        rb_f.partner_ids.add(self.partner)
        rb_f.type_id = self.rbt
        rb = rb_f.save()
        # Pending booking inherits location from type
        self.assertEqual(rb.state, "pending")
        self.assertEqual(rb.location, "Main office")
        # Booking can change location independently now
        with Form(rb) as rb_f:
            rb_f.location = "Office 3"
        self.assertEqual(self.rbt.location, "Main office")
        self.assertEqual(rb.location, "Office 3")
        # Changing booking type changes location
        with Form(rb) as rb_f:
            rb_f.type_id = rbt2
        self.assertEqual(rb.location, "Office 2")
        # Still can change it independently
        with Form(rb) as rb_f:
            rb_f.location = "Office 1"
        self.assertEqual(rb.location, "Office 1")
        self.assertEqual(rbt2.location, "Office 2")
        # Schedule the booking, meeting inherits location from it
        with Form(rb) as rb_f:
            rb_f.start = "2021-03-01 08:00:00"
        self.assertEqual(rb.state, "scheduled")
        self.assertEqual(rb.location, "Office 1")
        self.assertEqual(rb.meeting_id.location, "Office 1")
        # Changing meeting location changes location of booking
        with Form(rb.meeting_id) as meeting_f:
            meeting_f.location = "Office 2"
        self.assertEqual(rb.location, "Office 2")
        self.assertEqual(rb.meeting_id.location, "Office 2")
        # Changing booking location changes meeting location
        with Form(rb) as rb_f:
            rb_f.location = "Office 3"
        self.assertEqual(rb.meeting_id.location, "Office 3")
        self.assertEqual(rb.location, "Office 3")
        # When unscheduled, it keeps location untouched
        rb.action_unschedule()
        self.assertFalse(rb.meeting_id)
        self.assertEqual(rb.location, "Office 3")

    @mute_logger("odoo.models.unlink")
    def test_videocall_location(self):
        """Videocall location across records works as expected.
        We need to set dummy urls to prevent the _set_videocall_location() method
        of calendar from doing so."""
        rbt2 = self.rbt.copy({"videocall_location": "Videocall Office 2"})
        rb_f = Form(self.env["resource.booking"])
        rb_f.partner_ids.add(self.partner)
        rb_f.type_id = self.rbt
        rb = rb_f.save()
        # Pending booking inherits videocall location from type
        self.assertEqual(rb.state, "pending")
        self.assertEqual(rb.videocall_location, "Videocall Main office")
        # Booking can change videocall location independently now
        with Form(rb) as rb_f:
            rb_f.videocall_location = "Videocall Office 3"
        self.assertEqual(self.rbt.videocall_location, "Videocall Main office")
        self.assertEqual(rb.videocall_location, "Videocall Office 3")
        # Changing booking type changes videocall location
        with Form(rb) as rb_f:
            rb_f.type_id = rbt2
        self.assertEqual(rb.videocall_location, "Videocall Office 2")
        # Still can change it independently
        with Form(rb) as rb_f:
            rb_f.videocall_location = "https://Videocall Office 1"
        self.assertEqual(rb.videocall_location, "https://Videocall Office 1")
        self.assertEqual(rbt2.videocall_location, "Videocall Office 2")
        # Schedule the booking, meeting inherits videocall location from it
        with Form(rb) as rb_f:
            rb_f.start = "2021-03-01 08:00:00"
        self.assertEqual(rb.state, "scheduled")
        self.assertEqual(rb.videocall_location, "https://Videocall Office 1")
        self.assertEqual(rb.meeting_id.videocall_location, "https://Videocall Office 1")
        with Form(rb.meeting_id) as meeting_f:
            meeting_f.videocall_location = "https://Videocall Office 2"
        self.assertEqual(rb.videocall_location, "https://Videocall Office 2")
        self.assertEqual(rb.meeting_id.videocall_location, "https://Videocall Office 2")
        # Changing booking videocall location changes meeting location
        with Form(rb) as rb_f:
            rb_f.videocall_location = "https://Videocall Office 3"
        self.assertEqual(rb.videocall_location, "https://Videocall Office 3")
        self.assertEqual(rb.meeting_id.videocall_location, "https://Videocall Office 3")
        # When unscheduled, it keeps videocall location untouched
        rb.action_unschedule()
        self.assertFalse(rb.meeting_id)
        self.assertEqual(rb.videocall_location, "https://Videocall Office 3")

    def test_organizer_sync(self):
        """Resource booking and meeting organizers are properly synced."""
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "start": "2021-03-01 08:00:00",
                "duration": 1.5,
            }
        )
        self.assertEqual(rb.user_id, self.env.user)
        self.assertEqual(rb.meeting_id.user_id, self.env.user)
        rb.meeting_id.user_id = self.users[1]
        self.assertEqual(rb.user_id, self.users[1])
        self.assertEqual(rb.meeting_id.user_id, self.users[1])

    def test_resource_booking_display_name(self):
        # Pending booking with no name
        rb = self.env["resource.booking"].create(
            {"partner_ids": [(4, self.partner.id)], "type_id": self.rbt.id}
        )
        self.assertEqual(rb.display_name, "some customer - Test resource booking type")
        self.assertEqual(
            rb.with_context(using_portal=True).display_name, "# %d" % rb.id
        )
        # Pending booking with name
        rb.name = "changed"
        self.assertEqual(rb.display_name, "changed")
        self.assertEqual(
            rb.with_context(using_portal=True).display_name, "# %d - changed" % rb.id
        )
        # Scheduled booking with name
        rb.start = "2021-03-01 08:00:00"
        self.assertEqual(rb.display_name, "changed")
        self.assertEqual(
            rb.with_context(using_portal=True).display_name, "# %d - changed" % rb.id
        )
        # Scheduled booking with no name
        rb.name = False
        self.assertEqual(
            rb.display_name,
            "some customer - Test resource booking type - "
            f"{rb.meeting_id.display_time}",
        )
        self.assertEqual(
            rb.with_context(using_portal=True).display_name, "# %d" % rb.id
        )

    def test_attendee_autoassigned_not_autoconfirmed(self):
        """Meeting attendees are not autoconfirmed when combination is autoassigned."""
        # Create an auto-assigned booking
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "start": "2021-03-01 08:00:00",
            }
        )
        # Get attendees that belong to the combination human resource
        resource_partner = rb.combination_id.resource_ids.user_id.partner_id
        resource_attendees = rb.meeting_id.attendee_ids.filtered(
            lambda one: one.partner_id == resource_partner
        )
        # Combination was auto-assigned, so resource attendees are not confirmed
        self.assertEqual(resource_attendees.state, "needsAction")

    def test_attendee_not_autoassigned_autoconfirmed(self):
        """Meeting attendees are auto-confirmed when assigned by hand."""
        # Create a booking with handpicked combination assignment
        rb = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "start": "2021-03-01 08:00:00",
                "combination_auto_assign": False,
                "combination_id": self.rbcs[0].id,
            }
        )
        # Get attendees that belong to the combination human resources
        resource_partner = self.users[0].partner_id
        resource_attendees = rb.meeting_id.attendee_ids.filtered(
            lambda one: one.partner_id == resource_partner
        )
        # Combination was handpicked, so resource attendees are auto-confirmed
        self.assertEqual(resource_attendees.state, "accepted")

    def test_suggested_and_subscribed_recipients(self):
        self.env = self.env(context=dict(self.env.context, tracking_disable=False))
        # Create a booking as a new user
        rb_user = new_test_user(
            self.env, login="rbu", groups="base.group_user,resource_booking.group_user"
        )
        # Enable auto-subscription messaging
        with patch.object(self.env.registry, "ready", True):
            rb = (
                self.env["resource.booking"]
                .with_user(rb_user)
                .sudo()
                .create(
                    {
                        "partner_ids": [(4, self.partner.id)],
                        "type_id": self.rbt.id,
                        "combination_auto_assign": False,
                        "combination_id": self.rbcs[0].id,
                        "user_id": self.users[1].id,
                    }
                )
            )
        # Organizer, combination and creator must already be following
        self.assertEqual(
            rb.message_partner_ids, rb_user.partner_id | self.users[:2].partner_id
        )
        # Requester and combination must be suggested
        recipients_info = rb._message_get_suggested_recipients()
        self.assertEqual(len(recipients_info), 1)
        self.assertEqual(
            recipients_info[0],
            {
                "lang": None,
                "partner_id": rb.partner_ids.id,
                "name": "some customer",
                "display_name": "some customer",
                "reason": "Attendees",
            },
        )

    def test_creating_rbt_has_tags(self):
        """Creating booking works if type has tags."""
        categ = self.env["calendar.event.type"].create({"name": "test tag"})
        self.rbt.categ_ids = categ
        rb_f = Form(self.env["resource.booking"])
        rb_f.partner_ids.add(self.partner)
        rb_f.type_id = self.rbt
        rb = rb_f.save()
        self.assertEqual(rb.categ_ids, categ)

    def test_event_show_as_free(self):
        """Don't mind about event owner.

        Here I'll create 2 overlapping events. Since I create both, I'll be the
        owner of both automatically. However, there are 2 RBC available (one is
        me), so I still should be able to create 2 events.
        """
        user = self.users[0]
        rb_obj = self.env["resource.booking"].with_context(tracking_disable=True)
        # I'm the last option
        self.rbt.combination_assignment = "sorted"
        self.rbt.combination_rel_ids[0].sequence = 10
        # Create one long event on Monday, where there are 2 RBC available (one is me)
        rb_f = Form(rb_obj)
        rb_f.type_id = self.rbt
        rb_f.start = "2021-03-01 09:00:00"
        rb_f.duration = 1
        rb_f.partner_ids.add(self.partner)
        rb1 = rb_f.save()
        # I'm not booked, so I'm free
        self.assertEqual(rb1.combination_id, self.rbcs[2])
        self.assertNotIn(user.partner_id, rb1.meeting_id.partner_ids)
        # Create another event within the previous one
        rb_f = Form(rb_obj)
        rb_f.type_id = self.rbt
        rb_f.start = "2021-03-01 09:00:00"
        rb_f.duration = 1.5
        rb_f.partner_ids.add(self.partner.copy())
        # Saving works because I'm free
        rb2 = rb_f.save()
        # I'm booked this time, so I'm busy
        self.assertEqual(rb2.combination_id, self.rbcs[0])
        self.assertIn(user.partner_id, rb2.meeting_id.partner_ids)
        # Thus, it will fail without available resources on a next one
        rb_f = Form(rb_obj)
        rb_f.type_id = self.rbt
        rb_f.start = "2021-03-01 09:30:00"
        rb_f.duration = 0.5
        rb_f.partner_ids.add(self.partner.copy())
        with self.assertRaises(ValidationError):
            rb_f.save()

    def test_resource_is_available(self):
        """If a resource is involved in a booking or is not active at any point
        between two datetimes, then it is unavailable.
        """
        rbc_montue = self.rbcs[2]
        resource = rbc_montue.resource_ids[1]
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-01 08:00:00",
                "type_id": self.rbt.id,
                "combination_id": rbc_montue.id,
                "combination_auto_assign": False,
            }
        )
        # Resource is available on Monday at an unoccupied time.
        self.assertTrue(
            resource.is_available(
                utc.localize(datetime(2021, 3, 1, 10, 0)),
                utc.localize(datetime(2021, 3, 1, 11, 0)),
            )
        )
        # Resource is not available on Monday at an occupied time (longer than
        # booking).
        self.assertFalse(
            resource.is_available(
                utc.localize(datetime(2021, 3, 1, 7, 45)),
                utc.localize(datetime(2021, 3, 1, 8, 45)),
            )
        )
        # Resource is not available on Monday at an occupied time (within
        # booking time).
        self.assertFalse(
            resource.is_available(
                utc.localize(datetime(2021, 3, 1, 8, 10)),
                utc.localize(datetime(2021, 3, 1, 8, 20)),
            )
        )
        # Resource is not available on Monday at an occupied time (partially
        # overlaps booking).
        self.assertFalse(
            resource.is_available(
                utc.localize(datetime(2021, 3, 1, 8, 15)),
                utc.localize(datetime(2021, 3, 1, 8, 45)),
            )
        )
        # Resource is not available on Wednesdays.
        self.assertFalse(
            resource.is_available(
                utc.localize(datetime(2021, 3, 3, 10, 0)),
                utc.localize(datetime(2021, 3, 3, 11, 0)),
            )
        )

    def test_resource_is_available_span_days(self):
        # Correctly handle bookings that span across midnight.
        cal_satsun = self.r_calendars[3]
        rbc_satsun = self.rbcs[3]
        resource = rbc_satsun.resource_ids[1]
        self.rbt.resource_calendar_id = cal_satsun
        self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "start": "2021-03-06 23:00:00",
                "duration": 2,
                "type_id": self.rbt.id,
                "combination_id": rbc_satsun.id,
                "combination_auto_assign": False,
            }
        )
        self.assertFalse(
            resource.is_available(
                utc.localize(datetime(2021, 3, 6, 22, 0)),
                utc.localize(datetime(2021, 3, 7, 2, 0)),
            )
        )
        # Resource is available on the next weekend.
        self.assertTrue(
            resource.is_available(
                utc.localize(datetime(2021, 3, 13, 22, 0)),
                utc.localize(datetime(2021, 3, 14, 2, 0)),
            )
        )

    def test_resource_two_timezone(self):
        """
        Test that resource booking works correctly with two different time zones.
        - The resource calendar is set to the America/Guayaquil time zone
            and starts at 06:00.
        - The booking type has a calendar in the Europe/Madrid time zone
            and starts at 09:00.
        The slots should be returned in the same time zone as the booking type.
        The first slot should start at 06:00 in America/Guayaquil,
        which corresponds to 12:00 in Europe/Madrid.
        """
        calendar_friday = self.r_calendars[3]
        rbc_friday = self.rbcs[3]
        rbc_friday.resource_ids.write({"tz": "America/Guayaquil"})
        calendar_friday.write({"tz": "America/Guayaquil"})
        calendar_friday.attendance_ids.write({"hour_from": 6, "hour_to": 15})
        calendar_meeting = calendar_friday.copy(
            {"name": "Calendar Meeting", "tz": "Europe/Madrid"}
        )
        calendar_meeting.attendance_ids.write({"hour_from": 9, "hour_to": 14})
        self.rbt.write(
            {
                "resource_calendar_id": calendar_meeting.id,
                "min_advance_booking_hours": 2,
            }
        )
        resource_booking = self.env["resource.booking"].create(
            {
                "partner_ids": [(4, self.partner.id)],
                "duration": 2,
                "type_id": self.rbt.id,
                "combination_id": rbc_friday.id,
                "combination_auto_assign": False,
            }
        )
        response = resource_booking._get_calendar_context()
        for slots in response["slots"].values():
            # The first slot should start at 06:00 in America/Guayaquil.
            # In Europe/Madrid, it should be 12:00.
            self.assertEqual(slots[0].strftime("%H:%M:%S"), "12:00:00")
            # Check that all slots are in the expected time zone.
            for slot in slots:
                self.assertEqual(slot.tzinfo.zone, "Europe/Madrid")


class TestMailActivity(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.activity_type = cls.env.ref(
            "resource_booking.mail_activity_data_resource_booking"
        )
        cls.partner = cls.env["res.partner"].create({"name": "T Partner"})
        create_test_data(cls)
        cls.mail_activity = cls.env["mail.activity"].create(
            {
                "activity_type_id": cls.activity_type.id,
                "summary": "Test Summary",
                "res_model_id": cls.env["ir.model"]._get_id("res.partner"),
                "res_id": cls.partner.id,
            }
        )

    def _create_booking_from_mail_activity(self, mail_activity):
        action = mail_activity.action_open_resource_booking()
        booking_form = Form(
            self.env[action["res_model"]].with_context(**action["context"])
        )
        booking_form.start = "2021-03-01 08:00:00"
        booking_form.type_id = self.rbt
        booking_form.combination_auto_assign = False
        booking_form.combination_id = self.rbcs[2]
        booking_form.duration = 1
        booking_form.description = "Booking Description"
        return booking_form.save()

    @mute_logger("odoo.models.unlink")
    def test_action_open_resource_booking_full_process(self):
        action = self.mail_activity.action_open_resource_booking()
        self.assertEqual(action["res_id"], 0)
        self.assertEqual(action["view_mode"], "form")
        ctx = action["context"]
        self.assertEqual(ctx["default_activity_type_id"], self.activity_type.id)
        self.assertEqual(action["context"]["default_name"], "Test Summary")
        self.assertEqual(
            ctx["default_booking_activity_ids"],
            [Command.set([self.mail_activity.id])],
        )
        booking = self._create_booking_from_mail_activity(self.mail_activity)
        self.assertTrue(self.mail_activity.calendar_event_id)
        self.assertEqual(
            self.mail_activity.date_deadline, fields.Date.from_string("2021-03-01")
        )
        feedback = "Test Feedback"
        messages, activities = self.mail_activity._action_done(feedback=feedback)
        self.assertEqual(
            booking.description,
            "<p>Booking Description</p><br>Feedback: <p>Test Feedback</p>",
        )
        self.assertNotEqual(messages, [])
        self.assertNotEqual(activities, [])

    @mute_logger("odoo.models.unlink")
    def test_action_done_without_feedback(self):
        booking = self._create_booking_from_mail_activity(self.mail_activity)
        messages, activities = self.mail_activity._action_done()
        self.assertEqual(booking.description, "<p>Booking Description</p>")
        self.assertNotEqual(messages, [])
        self.assertNotEqual(activities, [])

    @freeze_time("2021-03-01 06:00:00")
    def test_sync_booking_activities(self):
        booking = self._create_booking_from_mail_activity(self.mail_activity)
        self.assertEqual(self.mail_activity.date_deadline, booking.start.date())
        booking.start = "2021-03-02 08:00:00"
        self.assertEqual(self.mail_activity.date_deadline, booking.start.date())

    @freeze_time("2021-03-01 06:00:00")
    def test_resource_booking_activity_without_date(self):
        booking = self.env["resource.booking"].create(
            {
                "name": "Test Booking",
                "description": "Booking Description",
                "type_id": self.rbt.id,
                "duration": 1,
                "booking_activity_ids": [
                    Command.create(
                        {
                            "activity_type_id": self.activity_type.id,
                            "summary": "Test Summary",
                            "note": "Test Note",
                            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
                            "res_id": self.partner.id,
                        },
                    )
                ],
            }
        )
        mail_activity = booking.booking_activity_ids[0]
        future_date = datetime.now() + relativedelta(years=1000)
        self.assertEqual(mail_activity.date_deadline, future_date.date())
        booking.combination_id = self.rbcs[2].id
        booking.combination_auto_assign = False
        booking.start = "2021-03-02 08:00:00"
        self.assertEqual(mail_activity.date_deadline, booking.start.date())

    @mute_logger("odoo.models.unlink")
    def test_unlink_resource_booking_activity(self):
        booking = self._create_booking_from_mail_activity(self.mail_activity)
        booking.action_cancel()
        self.assertNotEqual(
            self.mail_activity.date_deadline, fields.Date.from_string("2021-03-01")
        )
        self.assertFalse(self.mail_activity.calendar_event_id)

    @mute_logger("odoo.models.unlink")
    def test_resource_booking_schedule_unschedule(self):
        booking = self._create_booking_from_mail_activity(self.mail_activity)
        res = booking.action_schedule()
        self.assertTrue(self.mail_activity.calendar_event_id)
        self.env[res["res_model"]].with_context(**res["context"]).create(
            {"start": "2024-01-01 08:00:00", "stop": "2024-01-01 09:00:00"}
        )
        self.assertEqual(
            self.mail_activity.date_deadline, fields.Date.from_string("2024-01-01")
        )
        booking.action_unschedule()
        self.assertFalse(self.mail_activity.calendar_event_id)
        self.assertNotEqual(
            self.mail_activity.date_deadline, fields.Date.from_string("2024-01-01")
        )


@freeze_time("2021-02-26 09:00:00", tick=True)  # Last Friday of February
class BackendCaseCustom(BackendCaseBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=False))
        cls.mt_note = cls.env.ref("mail.mt_note")
        cls.mt_note.default = True
        cls.partner.email = "ŧest@test.com"

    def test_resource_booking_message_01(self):
        rb_model = self.env["resource.booking"]
        rb = rb_model.create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "combination_auto_assign": False,
                "combination_id": self.rbcs[0].id,
                "user_id": self.users[0].id,
            }
        )
        # Simulate the same as portal_booking_confirm
        booking_sudo = rb_model.sudo().browse(rb.id)
        booking_sudo = booking_sudo.with_context(
            using_portal=True, tz=booking_sudo.type_id.resource_calendar_id.tz
        )
        with Form(booking_sudo) as booking_form:
            booking_form.start = datetime(2021, 3, 1, 10)
        booking_sudo.action_confirm()
        meeting = rb.meeting_id
        follower = meeting.message_follower_ids.filtered(
            lambda x: x.partner_id == meeting.user_id.partner_id
        )
        self.assertIn(self.mt_note, follower.subtype_ids)
        messages = self.env["mail.message"].search(
            [
                ("model", "=", meeting._name),
                ("res_id", "=", meeting.id),
                ("message_type", "=", "user_notification"),
            ]
        )
        self.assertEqual(len(messages), 2)
        partner_message = messages.filtered(lambda x: self.partner in x.partner_ids)
        self.assertNotIn(rb.user_id.partner_id, partner_message.notified_partner_ids)
        user_message = messages.filtered(
            lambda x: meeting.user_id.partner_id in x.partner_ids
        )
        self.assertIn(meeting.user_id.partner_id, user_message.notified_partner_ids)

    def test_resource_booking_message_02(self):
        rb_model = self.env["resource.booking"]
        combination = self.rbcs[0]
        user_0 = self.users[0]
        user_1 = self.users[1]
        r_user_1 = self.r_users.filtered(lambda x: x.user_id == user_1)
        combination.write({"resource_ids": [(4, r_user_1.id)]})
        cal_mon = self.r_calendars[0]
        combination.forced_calendar_id = cal_mon
        rb = rb_model.create(
            {
                "partner_ids": [(4, self.partner.id)],
                "type_id": self.rbt.id,
                "combination_auto_assign": False,
                "combination_id": combination.id,
                "user_id": user_0.id,
            }
        )
        # Simulate the same as portal_booking_confirm
        booking_sudo = rb_model.sudo().browse(rb.id)
        booking_sudo = booking_sudo.with_context(
            using_portal=True, tz=booking_sudo.type_id.resource_calendar_id.tz
        )
        with Form(booking_sudo) as booking_form:
            booking_form.start = datetime(2021, 3, 1, 10)
        booking_sudo.action_confirm()
        meeting = rb.meeting_id
        follower = meeting.message_follower_ids.filtered(
            lambda x: x.partner_id == meeting.user_id.partner_id
        )
        self.assertIn(self.mt_note, follower.subtype_ids)
        messages = self.env["mail.message"].search(
            [
                ("model", "=", meeting._name),
                ("res_id", "=", meeting.id),
                ("message_type", "=", "user_notification"),
            ]
        )
        self.assertEqual(len(messages), 3)
        partner_message = messages.filtered(lambda x: self.partner in x.partner_ids)
        self.assertNotIn(user_0.partner_id, partner_message.notified_partner_ids)
        self.assertNotIn(user_1.partner_id, partner_message.notified_partner_ids)
        user_0_message = messages.filtered(lambda x: user_0.partner_id in x.partner_ids)
        self.assertIn(user_0.partner_id, user_0_message.notified_partner_ids)
        user_1_message = messages.filtered(lambda x: user_1.partner_id in x.partner_ids)
        self.assertIn(user_1.partner_id, user_1_message.notified_partner_ids)
