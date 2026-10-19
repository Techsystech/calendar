# Copyright 2021 Tecnativa - Jairo Llopis
# Copyright 2022 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .resource_booking import _BOOKING_DEADLINE_CHECKED


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    # One2one field, actually
    resource_booking_ids = fields.One2many(
        comodel_name="resource.booking",
        inverse_name="meeting_id",
        string="Resource booking",
    )

    @api.constrains("resource_booking_ids", "start", "stop")
    def _check_bookings_scheduling(self):
        """Scheduled bookings must have no conflicts."""
        bookings = self.sudo().resource_booking_ids
        return bookings._check_scheduling()

    def _validate_booking_modifications(self):
        """Check the original deadline before changing or canceling a booking."""
        bookings = self.sudo().resource_booking_ids
        # RPC context cannot forge this identity; IDs alone grant no exemption.
        if self.env.context.get("_booking_deadline_token") is _BOOKING_DEADLINE_CHECKED:
            checked_ids = self.env.context.get("_booking_deadline_event_ids", ())
            bookings = bookings.filtered(
                lambda booking: booking.meeting_id.id not in checked_ids
            )
        if bookings._can_override_scheduling():
            return
        # Booking.write may already have updated booking.start while syncing.
        # The meeting still holds the original schedule until this write/unlink.
        now = fields.Datetime.now()
        frozen = bookings.filtered(
            lambda booking: (
                booking.meeting_id.start
                and now
                > booking.meeting_id.start
                - timedelta(hours=booking.type_id.modifications_deadline)
            )
        )
        if frozen:
            raise ValidationError(
                self.env._(
                    "You are not allowed to alter these bookings because "
                    "they exceeded their modification deadlines:\n\n- %s",
                    "\n- ".join(frozen.mapped("display_name")),
                )
            )

    def unlink(self):
        """Check you're allowed to unschedule it."""
        self._validate_booking_modifications()
        return super().unlink()

    def write(self, vals):
        """Check you're allowed to reschedule it."""
        schedule_fields = vals.keys() & {
            "start",
            "stop",
            "duration",
            "allday",
            "start_date",
            "stop_date",
        }
        rescheduled = self.filtered(
            lambda event: any(
                event._fields[name].convert_to_cache(vals[name], event)
                != event._fields[name].convert_to_cache(event[name], event)
                for name in schedule_fields
            )
        )
        rescheduled._validate_booking_modifications()
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Transfer resource booking to _attendees_values by context.

        We need to serialize the creation in that case.
        mail_notify_author key from context is necessary to force the notification
        to be sent to author.
        """
        vals_list2 = []
        records = self.env["calendar.event"]
        for vals in vals_list:
            if "resource_booking_ids" in vals:
                records += super(
                    CalendarEvent,
                    self.with_context(
                        resource_booking_ids=vals["resource_booking_ids"],
                        mail_notify_author=True,
                    ),
                ).create(vals)
            else:
                vals_list2.append(vals)
        records += super().create(vals_list2)
        return records

    def get_interval(self, interval, tz=None):
        """Autofix tz from related resource booking.

        This function is called to render calendar.event notification mails.
        Any notification related to a resource.booking must be emitted in the
        same TZ as the resource.booking. Otherwise it's confusing to the user.
        """
        tz = self.resource_booking_ids.type_id.resource_calendar_id.tz or tz
        return super().get_interval(interval=interval, tz=tz)

    def _attendees_values(self, partner_commands):
        """Autoconfirm resource attendees if preselected and hand-picked on RB.

        NOTE: There's no support for changing `resource_booking_ids` once the meeting
        is created nor having more than one Rb attached to the same meeting, but that's
        not a real case for now.
        """
        attendee_commands = super()._attendees_values(partner_commands)
        partner_ids = False
        for cmd in self.env.context.get("resource_booking_ids", []):
            if cmd[0] == 0 and not cmd[2].get("combination_auto_assign", True):
                partner_ids = [cmd[2]["partner_id"]]
            elif cmd[0] == 6:
                rb = self.env["resource.booking"].browse(cmd[2])
                if rb.combination_auto_assign:
                    continue  # only auto-confirm if handpicked combination
                partner_ids = rb.combination_id.resource_ids.user_id.partner_id.ids
        for command in attendee_commands:
            if command[0] != 0:
                continue
            if not partner_ids:
                rb = self.sudo().resource_booking_ids
                partner_ids = rb.combination_id.resource_ids.user_id.partner_id.ids
            if command[2]["partner_id"] in partner_ids:
                command[2]["state"] = "accepted"
        return attendee_commands
