/* Copyright 2025 Ledo Enterprises LLC - Don Kendall
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

/**
 * Public booking page interactivity.
 *
 * Uses Odoo's publicWidget system so that the widget initializes correctly
 * with deferred/lazy asset loading in Odoo 18. Reads slot data from a
 * hidden data attribute rendered server-side and handles day/slot
 * selection without additional network requests.
 */

import publicWidget from "@web/legacy/js/public/public_widget";
import {cookie} from "@web/core/browser/cookie";
import {_t} from "@web/core/l10n/translation";

publicWidget.registry.WebsiteAppointmentBooking = publicWidget.Widget.extend({
    selector: ".o_wab_calendar",
    events: {
        "click .o_wab_day_available": "_onDayClick",
        "click .o_wab_slot_btn": "_onSlotClick",
        "change .o_wab_timezone_select": "_onTimezoneChange",
        "click .o_wab_combination_btn": "_onCombinationClick",
    },

    /**
     * @override
     */
    start() {
        this.dataEl = this.el.querySelector("#o_wab_slot_data");
        if (!this.dataEl) {
            return this._super(...arguments);
        }
        this.panel = this.el.querySelector("#o_wab_slots_panel");
        this.slotsTitle = this.panel
            ? this.panel.querySelector(".o_wab_slots_title")
            : null;
        this.slotsList = this.panel
            ? this.panel.querySelector(".o_wab_slots_list")
            : null;
        this.form = this.el.querySelector("#o_wab_form");
        this.whenInput = this.el.querySelector("#o_wab_when");
        this.selectedDisplay = this.el.querySelector("#o_wab_selected_display");
        this.slotsEmpty = this.el.querySelector("#o_wab_slots_empty");
        this.timezoneSelect = this.el.querySelector("#o_wab_timezone");
        this.timezoneInput = this.el.querySelector('input[name="tz"]');
        this.combinationInput = this.el.querySelector('input[name="combination_id"]');
        this.slotEndpoint = this.dataEl.dataset.slotEndpoint;
        this.displayedYear = this.dataEl.dataset.year;
        this.displayedMonth = this.dataEl.dataset.month;
        this._setSlotData(
            JSON.parse(this.dataEl.dataset.slots || "[]"),
            this.dataEl.dataset.selectedTz || "UTC",
            this.dataEl.dataset.selectedCombinationId || ""
        );
        return Promise.resolve(this._super(...arguments)).then(() => this._initTimezone());
    },

    async _initTimezone() {
        const locale = document.documentElement.lang.replace("_", "-") || undefined;
        const canonical = zone => {
            try {
                return new Intl.DateTimeFormat(undefined, {timeZone: zone}).resolvedOptions().timeZone;
            } catch (_error) {
                return zone; // Keep server zones newer than the browser's timezone data.
            }
        };
        const browserZone = canonical(Intl.DateTimeFormat().resolvedOptions().timeZone);
        const zones = new Map();
        const aliases = new Map();
        const common = ["America/New_York", "America/Chicago", "America/Denver",
            "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "UTC"];
        const rank = option => option.selected ? -3 : canonical(option.value) === browserZone ? -2 :
            common.includes(option.value) ? -1 : 0;
        const options = [...this.timezoneSelect.options].sort((a, b) => rank(a) - rank(b));
        for (const option of options) {
            let name = option.value;
            try {
                name = new Intl.DateTimeFormat(locale, {
                    timeZone: option.value, timeZoneName: "longGeneric",
                }).formatToParts().find(part => part.type === "timeZoneName").value;
            } catch (_error) {
                // Keep the server ID if this browser doesn't recognize the zone.
            }
            // Same name/offset today isn't enough: retain distinct future DST rules.
            const key = `${name}/${option.dataset.tzGroup}`;
            if (!zones.has(key)) {
                option.textContent = name;
                zones.set(key, option);
            }
            aliases.set(canonical(option.value), zones.get(key));
        }
        const labels = new Set();
        for (const option of zones.values()) {
            const label = option.textContent;
            if (labels.has(label)) {
                // A city is only needed to distinguish otherwise identical DST variants.
                option.textContent += ` — ${option.value.split("/").pop().replaceAll("_", " ")}`;
            }
            labels.add(label);
        }
        this.detectedTimezone = aliases.get(browserZone)?.value;
        this.timezoneSelect.replaceChildren(...[...zones.values()].sort((a, b) =>
            rank(a) - rank(b) || a.textContent.localeCompare(b.textContent, locale)
        ));
        this._syncTimezone();
        this._updateSelectionLinks();
        const preferred = new URL(window.location.href).searchParams.get("tz") || cookie.get("tz") || browserZone;
        try {
            const option = aliases.get(canonical(preferred));
            if (option && option.value !== this.selectedTimezone) {
                await this._applySelectionChange({timezone: option.value, combinationId: this.selectedCombinationId});
            }
        } catch (_error) {
            // Invalid/stale preferences leave the validated server default intact.
        }
        this.timezoneSelect.dataset.ready = "true";
    },

    _syncTimezone() {
        this.timezoneSelect.value = this.selectedTimezone;
        this.el.querySelector(".o_wab_tz_detected").classList.toggle(
            "d-none", this.selectedTimezone !== this.detectedTimezone
        );
    },

    _setSlotData(slotData, timezone, combinationId = "") {
        this.allSlots = slotData;
        this.selectedTimezone = timezone;
        this.selectedCombinationId = combinationId ? String(combinationId) : "";
        this.slotsByDate = {};
        this.availableDates = new Set();
        for (const slot of this.allSlots) {
            if (!this.slotsByDate[slot.date]) {
                this.slotsByDate[slot.date] = [];
            }
            this.slotsByDate[slot.date].push(slot);
            this.availableDates.add(slot.date);
        }
        this.dataEl.dataset.slots = JSON.stringify(this.allSlots);
        this.dataEl.dataset.selectedTz = this.selectedTimezone;
        this.dataEl.dataset.selectedCombinationId = this.selectedCombinationId;
        this._syncCombinationButtons();
    },

    _syncCombinationButtons(disabled = false) {
        this.el.querySelectorAll(".o_wab_combination_btn").forEach((button) => {
            const combinationId = button.dataset.combinationId || "";
            const isActive = combinationId === this.selectedCombinationId;
            button.classList.toggle("active", isActive);
            button.setAttribute("aria-pressed", isActive ? "true" : "false");
            button.disabled = disabled;
        });
    },

    _resetSelection() {
        this.el.querySelectorAll(".o_wab_day_selected").forEach((el) => {
            el.classList.remove("o_wab_day_selected");
        });
        if (this.panel) {
            this.panel.classList.add("d-none");
        }
        if (this.slotsList) {
            this.slotsList.innerHTML = "";
        }
        if (this.slotsTitle) {
            this.slotsTitle.textContent = "";
        }
        if (this.slotsEmpty) {
            this.slotsEmpty.classList.toggle("d-none", !this.allSlots.length);
        }
        if (this.form) {
            this.form.classList.add("d-none");
        }
        if (this.whenInput) {
            this.whenInput.value = "";
        }
        if (this.selectedDisplay) {
            this.selectedDisplay.textContent = "";
        }
    },

    _refreshAvailableDays() {
        this.el.querySelector(".o_wab_table").classList.toggle("d-none", !this.allSlots.length);
        this.el.querySelector(".o_wab_empty_month").classList.toggle("d-none", Boolean(this.allSlots.length));
        this.el.querySelectorAll(".o_wab_day").forEach((dayEl) => {
            const date = dayEl.dataset.date;
            if (!date) {
                return;
            }
            dayEl.classList.toggle("o_wab_day_available", this.availableDates.has(date));
            if (!this.availableDates.has(date)) {
                dayEl.classList.remove("o_wab_day_selected");
            }
        });
    },

    _getDayElement(date) {
        return this.el.querySelector(`.o_wab_day[data-date="${date}"]`);
    },

    _getSelectedDate() {
        return this.el.querySelector(".o_wab_day_selected")?.dataset.date || null;
    },

    _restoreSelectedDate(date) {
        if (!date || !this.availableDates.has(date)) {
            return false;
        }
        return this._showDateSlots(date, {
            dayEl: this._getDayElement(date),
        });
    },

    _showDateSlots(date, {dayEl = null, scroll = false} = {}) {
        const selectedDay = dayEl || this._getDayElement(date);
        if (!date || !selectedDay || !this.slotsByDate[date]) {
            return false;
        }

        this.el.querySelectorAll(".o_wab_day_selected").forEach((el) => {
            el.classList.remove("o_wab_day_selected");
        });
        selectedDay.classList.add("o_wab_day_selected");

        const dateObj = new Date(date + "T00:00:00");
        const dateStr = dateObj.toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
        });

        if (this.slotsTitle) {
            this.slotsTitle.textContent = dateStr;
        }
        if (this.slotsList) {
            this.slotsList.innerHTML = "";
            for (const slot of this.slotsByDate[date]) {
                const btn = this.el.ownerDocument.createElement("button");
                btn.type = "button";
                btn.className = "o_wab_slot_btn";
                btn.textContent = slot.time;
                btn.dataset.iso = slot.iso;
                btn.dataset.display = dateStr + " at " + slot.time;
                this.slotsList.appendChild(btn);
            }
        }
        if (this.panel) {
            this.panel.classList.remove("d-none");
        }
        if (this.slotsEmpty) {
            this.slotsEmpty.classList.add("d-none");
        }
        if (this.form) {
            this.form.classList.add("d-none");
        }
        if (scroll && this.panel) {
            this.panel.scrollIntoView({behavior: "smooth", block: "nearest"});
        }
        return true;
    },

    _updateSelectionLinks() {
        this.el.querySelectorAll(".o_wab_month_link").forEach((link) => {
            const url = new URL(link.href, window.location.origin);
            url.searchParams.set("tz", this.selectedTimezone);
            if (this.selectedCombinationId) {
                url.searchParams.set("combination_id", this.selectedCombinationId);
            } else {
                url.searchParams.delete("combination_id");
            }
            link.href = url.toString();
        });
    },

    async _fetchSlotData(timezone, combinationId = "") {
        const url = new URL(this.slotEndpoint, window.location.origin);
        url.searchParams.set("year", this.displayedYear);
        url.searchParams.set("month", this.displayedMonth);
        url.searchParams.set("tz", timezone);
        if (combinationId) {
            url.searchParams.set("combination_id", combinationId);
        }
        const response = await fetch(url.toString(), {
            headers: {Accept: "application/json"},
        });
        if (!response.ok) {
            throw new Error(`Failed to refresh booking slots (${response.status})`);
        }
        return response.json();
    },

    // -------------------------------------------------------------------------
    // Handlers
    // -------------------------------------------------------------------------

    /**
     * Handle click on an available calendar day.
     *
     * @param {Event} ev
     */
    _onDayClick(ev) {
        if (this.el.getAttribute("aria-busy") === "true") {
            return;
        }
        this._showDateSlots(ev.currentTarget.dataset.date, {
            dayEl: ev.currentTarget,
            scroll: true,
        });
    },

    /**
     * Handle click on a time slot button.
     *
     * @param {Event} ev
     */
    _onSlotClick(ev) {
        const btn = ev.currentTarget;

        if (this.panel) {
            this.panel.querySelectorAll(".o_wab_slot_btn").forEach((el) => {
                el.classList.remove("active");
            });
        }
        btn.classList.add("active");

        if (this.whenInput) {
            this.whenInput.value = btn.dataset.iso;
        }
        if (this.selectedDisplay) {
            this.selectedDisplay.textContent = btn.dataset.display;
        }
        if (this.form) {
            this.form.classList.remove("d-none");
            this.form.scrollIntoView({behavior: "smooth", block: "nearest"});
        }
    },

    async _applySelectionChange({timezone, combinationId}) {
        if (this.el.getAttribute("aria-busy") === "true") {
            return;
        }
        const selectedDate = this._getSelectedDate();
        const note = this.el.querySelector(".o_wab_tz_note");
        note.textContent = _t("Updating available times…");
        this.el.setAttribute("aria-busy", "true");
        this.timezoneSelect.disabled = true;
        this._syncCombinationButtons(true);
        this._resetSelection();
        try {
            const data = await this._fetchSlotData(timezone, combinationId);
            this._setSlotData(
                data.slot_data || [],
                data.selected_tz || timezone,
                data.selected_combination_id || ""
            );
            if (this.timezoneInput) {
                this.timezoneInput.value = this.selectedTimezone;
            }
            if (this.combinationInput) {
                this.combinationInput.value = this.selectedCombinationId;
            }
            this._refreshAvailableDays();
            this._updateSelectionLinks();
            this._resetSelection();
            this._restoreSelectedDate(selectedDate);
            const url = new URL(window.location.href);
            url.searchParams.set("tz", this.selectedTimezone);
            if (this.selectedCombinationId) {
                url.searchParams.set("combination_id", this.selectedCombinationId);
            } else {
                url.searchParams.delete("combination_id");
            }
            window.history.replaceState({}, "", url.toString());
            cookie.set("tz", this.selectedTimezone);
            note.textContent = _t("Displayed times update for the selected timezone.");
        } catch (_error) {
            note.textContent = _t("Could not update times. Please try again.");
        } finally {
            this._syncTimezone();
            this.timezoneSelect.disabled = false;
            this._syncCombinationButtons(false);
            this.el.setAttribute("aria-busy", "false");
        }
    },

    /**
     * Refresh slot data in-place for the selected timezone.
     *
     * @param {Event} ev
     */
    async _onTimezoneChange(ev) {
        const timezone = ev.currentTarget.value;
        if (!timezone || timezone === this.selectedTimezone) {
            return;
        }
        await this._applySelectionChange({timezone, combinationId: this.selectedCombinationId});
    },

    /**
     * Refresh slot data in-place for the selected resource combination.
     *
     * @param {Event} ev
     */
    async _onCombinationClick(ev) {
        const combinationId = ev.currentTarget.dataset.combinationId || "";
        if (combinationId === this.selectedCombinationId) {
            return;
        }
        await this._applySelectionChange({timezone: this.selectedTimezone, combinationId});
    },
});
