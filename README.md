# Success Computer Institute

Student test portal with course locks, demo test accounts, course-wise question banks, quiz scoring, detailed results, and admin controls.

## Run locally

```powershell
cd "C:\Users\msmei\OneDrive\Desktop\shivam.html"
python server.py
```

Open `http://127.0.0.1:8000`.

## Admin

Click the gear icon.

- User ID: `shivamsir`
- Password: `123`

## Demo student account

Enter a student name and 10-digit mobile number. The demo code/User ID is the normalized name plus the mobile's last two digits. Example: `Rahul Kumar` and `9876543210` becomes `rahulkumar10`.

## Excel format

Upload CSV or Excel from the admin panel after selecting the target course. Use these columns:

`course, question, optionA, optionB, optionC, optionD, answer`

The selected course receives the imported question bank; other courses are not changed.

## Render

The included `render.yaml` uses `python server.py` and Render's `PORT` variable. Commit the folder to GitHub, create a Render Web Service from that repository, and deploy. The current UI stores demo profiles, courses, and attempts in browser localStorage. For permanent multi-device student data, use Render PostgreSQL in a later backend integration.
