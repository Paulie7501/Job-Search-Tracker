# Job Search Tracker

A self-hosted job application tracker built with Flask and MySQL. Tracks applications, detects recruiter ghosting patterns, flags suspected fake adverts, and generates Job Seekers Allowance (JSA) evidence reports.

## Features

* Log and manage job applications with full status history
* Automatic ghost detection with four-tier risk system (None / Medium / High / Certain)
* Recruiter intelligence view with sortable ghost risk ratings
* Fake advert flagging
* JSA evidence log with printable PDF export
* Dashboard with application stats by status, source, and week

## Requirements

* Python 3.10+
* MySQL 8.0+

## Setup

### 1\. Clone the repository

```bash
git clone https://github.com/your-username/job-tracker.git
cd job-tracker
```

### 2\. Create a virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\\Scripts\\activate
pip install -r requirements.txt
```

### 3\. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your MySQL credentials:

```
DB\_HOST=
DB\_PORT=
DB\_USER=
DB\_PASSWORD=
DB\_NAME=job\_tracker
```

### 4\. Set up the database

```bash
mysql -u root -p < schema.sql
```

### 5\. Run the app

```bash
python app.py
```

Open your browser at `http://localhost:5012`

## Ghost Detection

The app runs an automated ghost check on startup and every hour in the background. Applications in an open status with no outcome date are tiered as follows:

|Tier|Condition|Action|
|-|-|-|
|None|Under 5 days|No flag|
|Medium|5-6 days, no response|Monitor|
|High|7+ days, no response|Likely ghosting|
|Certain|14+ days, no response|Status auto-set to Ghosted|

## Database Notes

The schema includes three useful views: `v\_dashboard`, `v\_recruiter\_stats`, and `v\_jsa\_evidence`. These power the dashboard, recruiters tab, and JSA export respectively. Run migrations automatically on first startup.

## JSA Evidence

The JSA tab generates a printable PDF of all job search activity suitable for submission to the DWP as evidence of active job seeking. Enter your claimant name and NI number before downloading.

## Licence

Copyright (C) 2026 Pauline A Harrison

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [GNU General Public License](https://www.gnu.org/licenses/gpl-3.0.html) for more details.

## Contact

info@KernEthik.com

