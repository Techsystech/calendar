To let some backend user to book resources:

1.  Go to *Settings \> Users & Companies \> Users*.
2.  Pick or create one.
3.  Assign *Resource Booking \> User*.

To let an internal backend user configure types and combinations, modify
overdue bookings, and override calendar availability when scheduling:

1.  Go to *Settings \> Users & Companies \> Users*.
2.  Pick or create one.
3.  Assign *Resource Booking \> Manager*.

To configure one booking type:

1.  Go to *Resource Bookings \> Types*.
2.  Create one.
3.  Give it a *name*.
4.  Set the *Duration*, to know the time assigned to each calendar slot.
    It will also be the default duration for each booking, although that
    can be changed later if necessary.
5.  Set the *Modifications Deadline*, to forbid non-managers to alter
    dates of a booking when it's too late.
6.  Choose one *Availability Calendar*. Normal bookings must fit within
    it. Internal booking managers can override availability in the backend.
7.  Under *Meeting defaults*, you will be able to fill some values that
    will be used by default on calendar meetings. These will appear in
    the global calendar when some booking is reserved.
8.  Choose some *Available resource combinations*. All combinations in
    the same line must be free to be booked together; otherwise the
    booking will not be able to be scheduled. You can sort them.
9.  Pick up one *Combination Assignment*. If you choose *Sorted*, then
    the order of the combinations you chose will indicate the one that
    is selected first. Of course, it must be free to be selected.
10. Save.

*Minimum Advance Booking (hours)* and *Maximum Advance Booking (days)*
restrict slots offered and accepted through the portal and public website.
A maximum of zero means no maximum advance limit. These settings are
independent of the modification deadline: a valid first booking can be made
inside that deadline, but later non-manager changes use the original start.
Managers using the portal or public website follow the same strict rules
as other visitors; sudo access alone does not grant a booking override.
