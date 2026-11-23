# EX2
## Classroom location

The classroom location follows the lecturer's laptop:

1. Open `/lecturer` and enter the lecturer key (asked once, then remembered by the browser).
2. Click **Allow** when the browser asks for location. The page sends the laptop's location to the server and updates it if the laptop moves or gets a more accurate fix.
3. Students are checked against that location, within `MAX_ALLOWED_RADIUS_METERS` (80 m). The student's reported GPS accuracy is not used to expand the attendance radius.

Accepted submissions with a missing or low-confidence student accuracy estimate, and submissions that would only fall inside the radius by relying on that estimate, are marked **Needs review** in the lecturer attendance list. This is a conservative warning, not proof of spoofing: a browser can still submit fabricated coordinates.

Until the laptop reports a location, the fixed `CLASSROOM_LAT` / `CLASSROOM_LON` in `attendance_app/config.py` is used.
Laptop readings worse than `MAX_LECTURER_ACCURACY_METERS` (300 m, typically IP-based guesses) are ignored.
Laptops locate themselves through Wi-Fi, so keep Wi-Fi on and enable Windows location services
(Settings → Privacy & security → Location).

### Lecturer key

`/lecturer`, the QR code, the attendance list and the location update all require the lecturer key.
Set it with the `LECTURER_KEY` environment variable (on Render: Dashboard → your service → Environment).
If it is not set, the default key is `change-me`.

## Running

Start the application with `python app.py`.
