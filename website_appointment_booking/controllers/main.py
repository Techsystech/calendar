# Copyright 2025 Ledo Enterprises LLC - Don Kendall
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import time
from urllib.parse import urlencode

import pytz
from werkzeug.exceptions import NotFound

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools.mail import plaintext2html

from odoo.addons.base.models.res_partner import _tz_get


class WebsiteAppointmentBooking(http.Controller):
    def _get_booking_type(self, slug):
        """Look up a published booking type by its slug."""
        return (
            request.env["resource.booking.type"]
            .sudo()
            .search(
                [("website_slug", "=", slug), ("website_published", "=", True)],
                limit=1,
            )
        )

    def _get_published_booking_types(self):
        """Return public booking types shown on the /book landing page."""
        return (
            request.env["resource.booking.type"]
            .sudo()
            .search(
                [
                    ("website_published", "=", True),
                    ("website_slug", "!=", False),
                ],
                order="name",
            )
        )

    def _get_booking_landing_website_page(self):
        """Return the standard website.page record that controls /book publishing."""
        page = request.env.ref(
            "website_appointment_booking.booking_landing_website_page",
            raise_if_not_found=False,
        )
        return page.sudo() if page else page

    @http.route(
        "/book",
        auth="public",
        type="http",
        website=True,
        sitemap=True,
    )
    def booking_landing_page(self, **kwargs):
        """Render the public booking landing page."""
        website_page = self._get_booking_landing_website_page()
        if (
            website_page
            and not website_page.website_published
            and not request.env.user.has_group("website.group_website_designer")
        ):
            raise NotFound()
        return request.render(
            "website_appointment_booking.booking_landing_page",
            {
                "booking_types": self._get_published_booking_types(),
                "main_object": website_page,
            },
        )

    def _get_timezone_options(self):
        """Return timezone options using the standard partner helper."""
        return _tz_get(request.env["res.partner"])

    def _get_selected_tz(self, booking_type, tz_name=None):
        """Return a validated timezone for public slot rendering."""
        available_tzs = {name for name, _label in self._get_timezone_options()}
        default_tz = booking_type.resource_calendar_id.tz or "UTC"
        if tz_name in available_tzs:
            return tz_name
        cookie_tz = request.httprequest.cookies.get("tz")
        if cookie_tz in available_tzs:
            return cookie_tz
        return default_tz

    def _get_booking_contact_prefill(self):
        """Return logged-in customer contact values for the public booking form."""
        if request.website.is_public_user():
            return {}
        partner = request.env.user.sudo().partner_id
        return {
            "name": partner.name or "",
            "email": partner.email or request.env.user.email or "",
            "phone": partner.phone or partner.mobile or "",
        }

    def _get_combination_options(self, booking_type):
        """Return ordered combination options for public resource selection."""
        return [
            {
                "id": rel.combination_id.id,
                "name": rel.combination_id.name,
            }
            for rel in booking_type.combination_rel_ids.sorted("sequence")
        ]

    def _get_selected_combination(self, booking_type, combination_id=None):
        """Return a validated booking combination for the public flow."""
        if not combination_id:
            return request.env["resource.booking.combination"]
        try:
            combination_id = int(combination_id)
        except (TypeError, ValueError):
            return request.env["resource.booking.combination"]
        return booking_type.combination_rel_ids.filtered(
            lambda rel: rel.combination_id.id == combination_id
        ).combination_id[:1]

    def _build_selection_query(self, selected_tz, selected_combination=None, **extra):
        """Build a query string preserving timezone/resource selection."""
        params = {}
        if selected_tz:
            params["tz"] = selected_tz
        if selected_combination:
            params["combination_id"] = selected_combination.id
        params.update({key: value for key, value in extra.items() if value})
        return f"?{urlencode(params)}" if params else ""

    def _get_slot_payload(
        self, booking_type, year=None, month=None, tz_name=None, combination_id=None
    ):
        """Return reusable slot payload data for page and AJAX refreshes."""
        selected_tz = self._get_selected_tz(booking_type, tz_name)
        selected_combination = self._get_selected_combination(
            booking_type, combination_id
        )
        phantom = self._create_phantom_booking(
            booking_type, tz=selected_tz, combination=selected_combination
        )
        calendar_ctx = phantom._get_calendar_context(year, month)
        lang = calendar_ctx["res_lang"]
        time_format = lang.time_format.replace(":%S", "")
        slot_data = self._serialize_slots(
            calendar_ctx["slots"], time_format, selected_tz
        )
        return {
            "calendar_ctx": calendar_ctx,
            "selected_tz": selected_tz,
            "selected_combination": selected_combination,
            "selected_combination_id": selected_combination.id or False,
            "slot_data": slot_data,
            "slot_data_json": json.dumps(slot_data),
            "selection_query": self._build_selection_query(
                selected_tz, selected_combination
            ),
            "available_dates": [day.isoformat() for day in calendar_ctx["slots"]],
        }

    def _create_phantom_booking(self, booking_type, tz=None, combination=None):
        """Create an in-memory booking for slot computation.

        Uses ``new()`` to avoid writing to the database. The phantom booking
        is configured with auto-assignment so that ``_get_available_slots``
        considers all resource combinations.
        """
        Booking = request.env["resource.booking"].sudo()
        tz = tz or booking_type.resource_calendar_id.tz or "UTC"
        values = {
            "type_id": booking_type.id,
            "duration": booking_type.duration,
            "combination_auto_assign": not bool(combination),
        }
        if combination:
            values["combination_id"] = combination.id
        return Booking.with_context(tz=tz, using_portal=True).new(values)

    def _serialize_slots(self, slots, time_format, tz_name):
        """Serialize slot data to a JSON-safe list of dicts.

        Each dict contains ``date``, ``time`` (display string) and ``iso``
        (full ISO 8601 value used for form submission).
        """
        display_tz = pytz.timezone(tz_name or "UTC")
        localized_slots = []
        for _day, times in sorted(slots.items()):
            for slot_dt in times:
                if slot_dt.tzinfo:
                    localized_slots.append(slot_dt.astimezone(display_tz))
                else:
                    localized_slots.append(
                        pytz.UTC.localize(slot_dt).astimezone(display_tz)
                    )
        result = []
        for slot_dt in sorted(localized_slots):
            result.append(
                {
                    "date": slot_dt.date().isoformat(),
                    "time": slot_dt.strftime(time_format),
                    "iso": slot_dt.isoformat(),
                }
            )
        return result

    def _prepare_last_booking_session(self, booking_type, when_tz_aware):
        return {
            "name": booking_type.name,
            "start": when_tz_aware.strftime("%B %d, %Y"),
            "time": when_tz_aware.strftime("%H:%M"),
            "duration": booking_type.duration,
            "location": booking_type.location or "",
        }

    def _finalize_booking(self, booking, booking_type, partner, selected_tz, selected_combination):
        """Finalize the booking after creation. Override in subclasses for payment flows."""
        booking.action_confirm()
        request.session["last_booking"] = self._prepare_last_booking_session(
            booking_type, booking.start
        )
        return request.redirect(f"/book/{booking_type.website_slug}/success")

    @http.route(
        [
            "/book/<slug>",
            "/book/<slug>/<int:year>/<int:month>",
        ],
        auth="public",
        type="http",
        website=True,
        sitemap=True,
    )
    def booking_page(self, slug, year=None, month=None, error=None, **kwargs):
        """Render the public booking page for a given booking type."""
        booking_type = self._get_booking_type(slug)
        if not booking_type:
            raise NotFound()
        slot_payload = self._get_slot_payload(
            booking_type,
            year,
            month,
            kwargs.get("tz"),
            kwargs.get("combination_id"),
        )
        values = {
            "booking_type": booking_type,
            "combination_options": self._get_combination_options(booking_type),
            "contact_prefill": self._get_booking_contact_prefill(),
            "slot_data": slot_payload["slot_data"],
            "slot_data_json": slot_payload["slot_data_json"],
            "selected_tz": slot_payload["selected_tz"],
            "selected_combination_id": slot_payload["selected_combination_id"],
            "timezone_options": self._get_timezone_options(),
            "selection_query": slot_payload["selection_query"],
            "error": error,
        }
        values.update(slot_payload["calendar_ctx"])
        return request.render("website_appointment_booking.booking_page", values)

    @http.route(
        "/book/<slug>/slots",
        auth="public",
        type="http",
        website=True,
        methods=["GET"],
    )
    def booking_slots(self, slug, year=None, month=None, **kwargs):
        """Return slot data for the selected month/timezone as JSON."""
        booking_type = self._get_booking_type(slug)
        if not booking_type:
            raise NotFound()
        year = year or kwargs.get("year")
        month = month or kwargs.get("month")
        year = int(year) if year else None
        month = int(month) if month else None
        slot_payload = self._get_slot_payload(
            booking_type,
            year,
            month,
            kwargs.get("tz"),
            kwargs.get("combination_id"),
        )
        return request.make_json_response(
            {
                "selected_tz": slot_payload["selected_tz"],
                "selected_combination_id": slot_payload["selected_combination_id"],
                "selection_query": slot_payload["selection_query"],
                "slot_data": slot_payload["slot_data"],
                "available_dates": slot_payload["available_dates"],
            }
        )

    def _check_rate_limit(self):
        """Anti-spam: reject if honeypot filled or rate limit exceeded.

        Returns (ok, redirect_url) tuple. ok=True means proceed.
        """
        # Honeypot check — bots fill invisible fields
        if request.httprequest.form.get("website"):
            return False, None

        now = time.time()
        session = request.session
        last_submit = session.get("wab_last_submit", 0)
        submit_count = session.get("wab_submit_count", 0)
        submit_hour = session.get("wab_submit_hour", 0)
        current_hour = int(now // 3600)

        if current_hour != submit_hour:
            session["wab_submit_hour"] = current_hour
            session["wab_submit_count"] = 0
            submit_count = 0

        # Minimum 5 seconds between submissions
        if now - last_submit < 5:
            return False, "Please wait a moment before submitting again."

        # Max 10 submissions per hour per session
        if submit_count >= 10:
            return False, "Too many booking attempts. Please try again later."

        session["wab_last_submit"] = now
        session["wab_submit_count"] = submit_count + 1
        return True, None

    @http.route(
        "/book/<slug>/confirm",
        auth="public",
        type="http",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def booking_confirm(self, slug, **kwargs):
        """Process a booking confirmation.

        Expects POST parameters ``name``, ``email``, ``phone`` and ``when``
        (ISO 8601).
        Creates or finds the partner, creates the booking, assigns the slot
        and confirms.
        """
        booking_type = self._get_booking_type(slug)
        if not booking_type:
            raise NotFound()

        ok, error_msg = self._check_rate_limit()
        if not ok:
            selected_tz = self._get_selected_tz(booking_type, kwargs.get("tz"))
            selected_combination = self._get_selected_combination(
                booking_type, kwargs.get("combination_id")
            )
            return request.redirect(
                f"/book/{slug}{self._build_selection_query(selected_tz, selected_combination, error=error_msg or 'Unable to process request.')}"
            )

        selected_tz = self._get_selected_tz(booking_type, kwargs.get("tz"))
        selected_combination = self._get_selected_combination(
            booking_type, kwargs.get("combination_id")
        )
        name = (kwargs.get("name") or "").strip()
        email = (kwargs.get("email") or "").strip()
        phone = (kwargs.get("phone") or "").strip()
        discussion = (kwargs.get("discussion") or "").strip()
        when_str = kwargs.get("when", "")
        if not name or not email or not phone or not when_str:
            return request.redirect(
                f"/book/{slug}{self._build_selection_query(selected_tz, selected_combination, error='Please fill in all fields.')}"
            )
        # Validate before creating or updating contact details, including for managers
        # using the public website. The phantom preserves resource selection semantics.
        phantom = self._create_phantom_booking(
            booking_type, tz=selected_tz, combination=selected_combination
        )
        try:
            when_naive = phantom._validate_submitted_slot(when_str)
        except ValidationError as error:
            query = self._build_selection_query(
                selected_tz, selected_combination, error=str(error)
            )
            return request.redirect(f"/book/{slug}{query}")
        # Find or create partner
        Partner = request.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", email)], limit=1)
        if not partner:
            partner = Partner.create({"name": name, "email": email, "phone": phone})
        else:
            partner_vals = {}
            if not partner.name or partner.name == email:
                partner_vals["name"] = name
            if phone and not partner.phone:
                partner_vals["phone"] = phone
            if partner_vals:
                partner.write(partner_vals)
        # Create and schedule the booking inside a savepoint so that
        # a ValidationError (race condition: slot already taken) can be
        # caught without poisoning the database cursor.
        Booking = request.env["resource.booking"].sudo()
        tz = booking_type.resource_calendar_id.tz or "UTC"
        try:
            with request.env.cr.savepoint():
                booking = Booking.with_context(
                    tz=tz,
                    using_portal=True,
                    mail_create_nosubscribe=True,
                ).create(
                    {
                        "type_id": booking_type.id,
                        "partner_ids": [(4, partner.id)],
                        "description": plaintext2html(discussion) if discussion else False,
                        "combination_auto_assign": not bool(selected_combination),
                        "combination_id": selected_combination.id or False,
                    }
                )
                booking.start = when_naive
        except ValidationError:
            # Race condition: slot was taken between page load and submit
            month_str = f"{when_naive:%Y/%m}"
            return request.redirect(
                f"/book/{slug}/{month_str}"
                f"{self._build_selection_query(selected_tz, selected_combination, error='That slot is no longer available. Please choose another.')}"
            )
        return self._finalize_booking(
            booking, booking_type, partner, selected_tz, selected_combination
        )

    @http.route(
        "/book/<slug>/success",
        auth="public",
        type="http",
        website=True,
    )
    def booking_success(self, slug, **kwargs):
        """Thank-you page after a successful booking."""
        booking_type = self._get_booking_type(slug)
        if not booking_type:
            raise NotFound()
        last_booking = request.session.pop("last_booking", {})
        values = {
            "booking_type": booking_type,
            "last_booking": last_booking,
        }
        return request.render("website_appointment_booking.booking_success", values)
