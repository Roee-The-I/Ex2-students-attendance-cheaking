# EX2
## Classroom location

The classroom location follows the lecturer's laptop:

1. Open `/lecturer` and enter the lecturer key (asked once, then remembered by the browser).
2. Click **Allow** when the browser asks for location. The page sends the laptop's location to the server and updates it if the laptop moves or gets a more accurate fix.
3. Students are checked against that location, within `MAX_ALLOWED_RADIUS_METERS` (80 m), allowing for the accuracy of both the phone and the laptop.

Until the laptop reports a location, the fixed `CLASSROOM_LAT` / `CLASSROOM_LON` in `EX2.py` is used.
Laptop readings worse than `MAX_LECTURER_ACCURACY_METERS` (300 m, typically IP-based guesses) are ignored.
Laptops locate themselves through Wi-Fi, so keep Wi-Fi on and enable Windows location services
(Settings → Privacy & security → Location).

### Lecturer key

`/lecturer`, the QR code, the attendance list and the location update all require the lecturer key.
Set it with the `LECTURER_KEY` environment variable (on Render: Dashboard → your service → Environment).
If it is not set, the default key is `change-me`.
