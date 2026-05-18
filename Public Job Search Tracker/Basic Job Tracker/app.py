# Job Search Tracker - Flask/MySQL application for tracking job applications,
# detecting recruiter ghosting patterns and generating JSA evidence reports.
# Copyright (C) 2026  Pauline A Harrison
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Contact: info@KernEthik.com

"""
Job Search Tracker - Flask + MySQL
Run: python app.py
Access: 
"""
import os
import threading
import time
from flask import Flask, render_template, request, jsonify, send_file
from datetime import date, datetime, timedelta
import pymysql
import pymysql.cursors
import io
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# ─── DB CONFIG ────────────────────────────────────────────────────────────────
# Copy .env.example to .env and fill in your values before running.
DB_CONFIG = {
    'host':     os.getenv('DB_HOST', '<host>'),
    'port':     int(os.getenv('DB_PORT', <port>)),
    'user':     os.getenv('DB_USER', '<user>'),
    'password': os.getenv('DB_PASSWORD', '<password>'),
    'db':       os.getenv('DB_NAME', 'job_tracker'),
    'charset':  'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
}

def get_db():
    return pymysql.connect(**DB_CONFIG)


def run_migrations():
    """Apply any schema changes needed on existing databases."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            # Add ghost_risk_tier column if it doesn't exist
            cur.execute("""
                SELECT COUNT(*) as n FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = 'job_tracker'
                AND TABLE_NAME = 'applications'
                AND COLUMN_NAME = 'ghost_risk_tier'
            """)
            if cur.fetchone()['n'] == 0:
                cur.execute("""
                    ALTER TABLE applications
                    ADD COLUMN ghost_risk_tier VARCHAR(10) DEFAULT 'None'
                    AFTER ghost_score
                """)
                print('Migration: ghost_risk_tier column added')

            # Update URN 40 to new status
            cur.execute("""
                UPDATE applications
                SET status = 'Recruiter Call - Progressing'
                WHERE urn = 40
                AND status NOT IN ('Rejected','Offer','Withdrawn','Ghosted')
            """)
            if cur.rowcount:
                print('Migration: URN 40 updated to Recruiter Call - Progressing')

            # Clean up any 0000-00-00 zero dates left by old code
            cur.execute("""
                UPDATE applications
                SET outcome_date = NULL
                WHERE NULLIF(outcome_date, '0000-00-00') IS NULL
                AND outcome_date IS NOT NULL
            """)
            if cur.rowcount:
                print(f'Migration: cleared {cur.rowcount} zero-date outcome_date rows')

        conn.commit()
    finally:
        conn.close()


def query(sql, args=None, one=False):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.close()


def execute(sql, args=None):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.lastrowid
    finally:
        conn.close()


# ─── DATE HELPER ─────────────────────────────────────────────────────────────
def _serial(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def serialise(rows):
    if isinstance(rows, dict):
        return {k: _serial(v) for k, v in rows.items()}
    return [{k: _serial(v) for k, v in row.items()} for row in rows]


# ═════════════════════════════════════════════════════════════════════════════
# PAGES
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


# ═════════════════════════════════════════════════════════════════════════════
# API - DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

REAL = "company IS NOT NULL AND company != '' AND role IS NOT NULL AND role != ''"

@app.route('/api/dashboard')
def api_dashboard():
    stats = query('SELECT * FROM v_dashboard', one=True)
    by_status = query(
        f'SELECT status, COUNT(*) as cnt FROM applications WHERE {REAL} GROUP BY status ORDER BY cnt DESC'
    )
    by_source = query(
        f'SELECT source, COUNT(*) as cnt FROM applications WHERE {REAL} AND source IS NOT NULL '
        f'GROUP BY source ORDER BY cnt DESC'
    )
    by_week = query(
        f'SELECT DATE_SUB(date_applied, INTERVAL WEEKDAY(date_applied) DAY) as week, '
        f'COUNT(*) as cnt FROM applications WHERE {REAL} AND date_applied IS NOT NULL '
        f'GROUP BY week ORDER BY week DESC LIMIT 8'
    )
    return jsonify({
        'stats': serialise(stats),
        'by_status': serialise(by_status),
        'by_source': serialise(by_source),
        'by_week': serialise(by_week),
    })


# ═════════════════════════════════════════════════════════════════════════════
# API - APPLICATIONS (full CRUD)
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/applications')
def api_applications():
    search  = request.args.get('search', '').strip()
    status  = request.args.get('status', '')
    source  = request.args.get('source', '')
    loc     = request.args.get('location', '')
    page    = int(request.args.get('page', 1))
    per     = int(request.args.get('per', 50))
    offset  = (page - 1) * per

    where, args = ['company IS NOT NULL', 'company != ""', 'role IS NOT NULL', 'role != ""'], []
    if search:
        where.append('(company LIKE %s OR role LIKE %s OR contact_name LIKE %s)')
        args += [f'%{search}%', f'%{search}%', f'%{search}%']
    if status:
        where.append('status = %s'); args.append(status)
    if source:
        where.append('source = %s'); args.append(source)
    if loc:
        where.append('location_type LIKE %s'); args.append(f'%{loc}%')

    w = ' AND '.join(where)
    total = query(f'SELECT COUNT(*) as n FROM applications WHERE {w}', args, one=True)['n']
    rows  = query(
        f'SELECT * FROM applications WHERE {w} ORDER BY date_applied DESC, urn DESC '
        f'LIMIT %s OFFSET %s',
        args + [per, offset]
    )
    return jsonify({'total': total, 'page': page, 'per': per, 'rows': serialise(rows)})


@app.route('/api/applications', methods=['POST'])
def api_add_application():
    d = request.json
    # Auto-generate URN if not supplied
    if not d.get('urn'):
        max_urn = query('SELECT MAX(urn) as m FROM applications', one=True)['m'] or 0
        d['urn'] = max_urn + 1
    # Auto-generate JSA evidence text
    if not d.get('jsa_evidence') and d.get('company') and d.get('role') and d.get('date_applied'):
        dt = d['date_applied']
        d['jsa_evidence'] = f"Applied to {d['company']} for {d['role']} on {dt}"

    lid = execute(
        '''INSERT INTO applications
           (urn, date_applied, source, company, role, job_url, contact_name, contact_email,
            status, outcome_date, interview1_date, interview2_date, salary, location_type,
            notes, ghost_score, fake_advert, days_to_response, jsa_evidence)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (d.get('urn'), d.get('date_applied'), d.get('source'), d.get('company'),
         d.get('role'), d.get('job_url'), d.get('contact_name'), d.get('contact_email'),
         d.get('status', 'Applied'), d.get('outcome_date'), d.get('interview1_date'),
         d.get('interview2_date'), d.get('salary'), d.get('location_type'),
         d.get('notes'), d.get('ghost_score', 0), 1 if d.get('fake_advert') else 0,
         d.get('days_to_response'), d.get('jsa_evidence'))
    )
    row = query('SELECT * FROM applications WHERE id = %s', (lid,), one=True)
    return jsonify(serialise(row)), 201


@app.route('/api/applications/<int:app_id>', methods=['GET'])
def api_get_application(app_id):
    row = query('SELECT * FROM applications WHERE id = %s', (app_id,), one=True)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(serialise(row))


@app.route('/api/applications/<int:app_id>', methods=['PUT'])
def api_update_application(app_id):
    d = request.json
    # Recalculate days_to_response if outcome_date set
    if d.get('outcome_date') and d.get('date_applied'):
        try:
            d1 = datetime.strptime(d['date_applied'], '%Y-%m-%d').date()
            d2 = datetime.strptime(d['outcome_date'], '%Y-%m-%d').date()
            d['days_to_response'] = (d2 - d1).days
        except Exception:
            pass
    execute(
        '''UPDATE applications SET
           date_applied=%s, source=%s, company=%s, role=%s, job_url=%s,
           contact_name=%s, contact_email=%s, status=%s, outcome_date=%s,
           interview1_date=%s, interview2_date=%s, salary=%s, location_type=%s,
           notes=%s, ghost_score=%s, fake_advert=%s, days_to_response=%s, jsa_evidence=%s
           WHERE id=%s''',
        (d.get('date_applied'), d.get('source'), d.get('company'), d.get('role'),
         d.get('job_url'), d.get('contact_name'), d.get('contact_email'),
         d.get('status', 'Applied'), d.get('outcome_date') or None,
         d.get('interview1_date') or None, d.get('interview2_date') or None,
         d.get('salary'), d.get('location_type'), d.get('notes'),
         d.get('ghost_score', 0), 1 if d.get('fake_advert') else 0,
         d.get('days_to_response'), d.get('jsa_evidence'), app_id)
    )
    row = query('SELECT * FROM applications WHERE id = %s', (app_id,), one=True)
    return jsonify(serialise(row))


@app.route('/api/applications/<int:app_id>', methods=['DELETE'])
def api_delete_application(app_id):
    execute('DELETE FROM applications WHERE id = %s', (app_id,))
    return jsonify({'deleted': app_id})


# ═════════════════════════════════════════════════════════════════════════════
# API - RECRUITER INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════════════


@app.route('/api/recruiters')
def api_recruiters():
    rows = query('SELECT * FROM v_recruiter_stats')
    return jsonify(serialise(rows))


# ═════════════════════════════════════════════════════════════════════════════
# API - JSA EVIDENCE
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/jsa')
def api_jsa():
    week = request.args.get('week', '')
    where, args = ['date_applied IS NOT NULL'], []
    if week:
        where.append('DATE_SUB(date_applied, INTERVAL WEEKDAY(date_applied) DAY) = %s')
        args.append(week)
    w = ' AND '.join(where)
    rows = query(f'SELECT * FROM v_jsa_evidence WHERE {w}', args)
    weeks = query(
        'SELECT DISTINCT DATE_SUB(date_applied, INTERVAL WEEKDAY(date_applied) DAY) as week '
        'FROM applications WHERE date_applied IS NOT NULL ORDER BY week DESC'
    )
    return jsonify({'rows': serialise(rows), 'weeks': serialise(weeks)})


@app.route('/api/jsa/pdf')
def api_jsa_pdf():
    week = request.args.get('week', '')
    claimant = request.args.get('claimant', 'Job Seeker')
    ni = request.args.get('ni', '')

    where, args = ['date_applied IS NOT NULL'], []
    if week:
        where.append('DATE_SUB(date_applied, INTERVAL WEEKDAY(date_applied) DAY) = %s')
        args.append(week)
    w = ' AND '.join(where)
    rows = query(f'SELECT * FROM v_jsa_evidence WHERE {w} ORDER BY date_applied ASC', args)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        title='JSA Job Search Evidence'
    )

    styles = getSampleStyleSheet()
    navy = colors.HexColor('#1B2A4A')
    teal = colors.HexColor('#2E7D6B')

    title_style = ParagraphStyle('Title', parent=styles['Normal'],
        fontSize=16, textColor=navy, spaceAfter=4, fontName='Helvetica-Bold',
        alignment=TA_CENTER)
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'],
        fontSize=10, textColor=teal, spaceAfter=2, alignment=TA_CENTER)
    meta_style = ParagraphStyle('Meta', parent=styles['Normal'],
        fontSize=9, spaceAfter=12, alignment=TA_CENTER)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'],
        fontSize=7.5, leading=10)

    story = []
    story.append(Paragraph("JOBSEEKER'S ALLOWANCE", title_style))
    story.append(Paragraph("Job Search Evidence Record", sub_style))

    week_label = week if week else 'All weeks'
    story.append(Paragraph(
        f'Claimant: <b>{claimant}</b> &nbsp;&nbsp; NI Number: <b>{ni}</b> &nbsp;&nbsp; '
        f'Week commencing: <b>{week_label}</b> &nbsp;&nbsp; Generated: <b>{date.today().strftime("%d/%m/%Y")}</b>',
        meta_style
    ))
    story.append(Spacer(1, 0.3*cm))

    headers = [
        'Week\nCommencing', 'Date', 'Employer /\nOrganisation', 'Role Applied For',
        'How Applied', 'Source', 'Contact', 'Outcome /\nResponse', 'Evidence'
    ]

    col_widths = [2.4*cm, 2.0*cm, 4.0*cm, 6.0*cm, 2.0*cm, 2.4*cm, 2.4*cm, 2.8*cm, 4.0*cm]

    def fmt_date(v):
        if not v: return ''
        try:
            if isinstance(v, str):
                return datetime.strptime(v, '%Y-%m-%d').strftime('%d/%m/%Y')
            return v.strftime('%d/%m/%Y')
        except Exception:
            return str(v)

    def status_to_outcome(s):
        mapping = {
            'Applied': 'Awaiting Response',
            'Applied - Warm Contact': 'Awaiting Response',
            'Applied via recruiter': 'Awaiting Response',
            'Recruiter Call - Progressing': 'Recruiter Call - Progressing',
            'Recruiter Call - Further Info Req': 'Recruiter Call - Further Info',
            'Rejected': 'Rejected',
            'Offer': 'Offer Made',
            'Interview Booked': 'Interview Arranged',
            '2nd Interview': '2nd Interview',
            'Ghosted': 'No Response (Ghosted)',
            'Withdrawn': 'Withdrawn',
        }
        return mapping.get(s or '', 'Awaiting Response')

    data = [[Paragraph(h, ParagraphStyle('H', fontName='Helvetica-Bold',
             fontSize=7.5, leading=10, textColor=colors.white)) for h in headers]]

    for r in rows:
        wc = ''
        try:
            da = r.get('date_applied') or r.get('week_commencing')
            if da:
                if isinstance(da, str):
                    da = datetime.strptime(da, '%Y-%m-%d').date()
                from datetime import timedelta
                wc_date = da - timedelta(days=da.weekday())
                wc = wc_date.strftime('%d/%m/%Y')
        except Exception:
            wc = ''

        data.append([
            Paragraph(wc, cell_style),
            Paragraph(fmt_date(r.get('date_applied')), cell_style),
            Paragraph(str(r.get('company') or ''), cell_style),
            Paragraph(str(r.get('role') or ''), cell_style),
            Paragraph('Online', cell_style),
            Paragraph(str(r.get('source') or ''), cell_style),
            Paragraph(str(r.get('contact_name') or ''), cell_style),
            Paragraph(status_to_outcome(r.get('status')), cell_style),
            Paragraph(str(r.get('jsa_evidence') or 'Application confirmation'), cell_style),
        ])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), navy),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#EEF2F7')]),
        ('GRID',       (0,0), (-1,-1), 0.4, colors.HexColor('#C0C8D8')),
        ('VALIGN',     (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING',  (0,0), (-1,-1), 4),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.5*cm))

    total_para = Paragraph(
        f'<b>Total job search activities this period: {len(rows)}</b>',
        ParagraphStyle('Tot', parent=styles['Normal'], fontSize=9, textColor=navy)
    )
    story.append(total_para)
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        'I confirm that the above information is a true and accurate record of my job search activities.',
        ParagraphStyle('Confirm', parent=styles['Normal'], fontSize=8, textColor=colors.grey)
    ))
    story.append(Spacer(1, 1.0*cm))
    story.append(Paragraph(
        'Signature: _________________________ &nbsp;&nbsp; Date: _________________________',
        ParagraphStyle('Sig', parent=styles['Normal'], fontSize=9)
    ))

    doc.build(story)
    buf.seek(0)
    filename = f'JSA_Evidence_{week or "all"}.pdf'
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=filename)


# ═════════════════════════════════════════════════════════════════════════════
# API - FILTER OPTIONS
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/options')
def api_options():
    statuses = query('SELECT DISTINCT status FROM applications WHERE status IS NOT NULL ORDER BY status')
    sources  = query('SELECT DISTINCT source FROM applications WHERE source IS NOT NULL ORDER BY source')
    locs     = query('SELECT DISTINCT location_type FROM applications WHERE location_type IS NOT NULL ORDER BY location_type')
    return jsonify({
        'statuses': [r['status'] for r in statuses],
        'sources':  [r['source'] for r in sources],
        'locations': [r['location_type'] for r in locs],
    })



# ═════════════════════════════════════════════════════════════════════════════
# AUTO-GHOSTING - background thread + manual API
# ═════════════════════════════════════════════════════════════════════════════

GHOST_STATUSES = {'Applied', 'Applied - Warm Contact', 'Applied via recruiter',
                  'Recruiter Call - Progressing', 'Recruiter Call - Further Info Req'}

# Ghost tier thresholds (days since application, no outcome)
# None     : < 7 days
# Medium   : approaching 7 days (5-6 days)
# High     : >= 7 days
# Certain  : >= 14 days  ->  also flips status to 'Ghosted'

def run_ghost_check():
    """
    Four-tier ghost risk system applied to all open applications with no outcome.
    Tier is stored in ghost_risk_tier. Certain (14+ days) also sets status = Ghosted.
    """
    today = datetime.now().date()

    # Cutoffs
    certain_cutoff = (today - timedelta(days=14)).strftime('%Y-%m-%d')
    high_cutoff    = (today - timedelta(days=7)).strftime('%Y-%m-%d')
    medium_cutoff  = (today - timedelta(days=5)).strftime('%Y-%m-%d')

    no_outcome = ("NULLIF(outcome_date, '0000-00-00') IS NULL "
                  "AND company IS NOT NULL AND company != '' "
                  "AND date_applied IS NOT NULL")

    open_statuses = ("status IN ('Applied','Applied - Warm Contact','Applied via recruiter',"
                     "'Recruiter Call - Progressing','Recruiter Call - Further Info Req')")

    # Tier 1: Certain (>= 14 days) - set status to Ghosted
    execute(
        f"""UPDATE applications
            SET ghost_risk_tier = 'Certain',
                ghost_score = GREATEST(ghost_score, 4),
                status = 'Ghosted'
            WHERE {open_statuses}
            AND date_applied <= %s
            AND {no_outcome}""",
        (certain_cutoff,)
    )

    # Tier 2: High (>= 7 days, < 14 days)
    execute(
        f"""UPDATE applications
            SET ghost_risk_tier = 'High',
                ghost_score = GREATEST(ghost_score, 2)
            WHERE {open_statuses}
            AND date_applied <= %s AND date_applied > %s
            AND {no_outcome}""",
        (high_cutoff, certain_cutoff)
    )

    # Tier 3: Medium (5-6 days)
    execute(
        f"""UPDATE applications
            SET ghost_risk_tier = 'Medium',
                ghost_score = GREATEST(ghost_score, 1)
            WHERE {open_statuses}
            AND date_applied <= %s AND date_applied > %s
            AND {no_outcome}""",
        (medium_cutoff, high_cutoff)
    )

    # Tier 4: None (< 5 days) - clear any stale tier on recently added records
    execute(
        f"""UPDATE applications
            SET ghost_risk_tier = 'None',
                ghost_score = 0
            WHERE {open_statuses}
            AND date_applied > %s
            AND {no_outcome}""",
        (medium_cutoff,)
    )


def ghost_check_loop():
    """Run ghost check every hour in the background."""
    time.sleep(10)  # brief delay on startup
    while True:
        try:
            run_ghost_check()
        except Exception as e:
            print(f'Ghost check error: {e}')
        time.sleep(3600)  # run every hour


@app.route('/api/ghost/check', methods=['POST'])
def api_ghost_check():
    """Manually trigger the ghost check."""
    run_ghost_check()
    counts = query(
        """SELECT ghost_risk_tier, COUNT(*) as n
           FROM applications
           WHERE ghost_risk_tier IS NOT NULL AND ghost_risk_tier != 'None'
           GROUP BY ghost_risk_tier""",
    )
    ghosted = query(
        "SELECT COUNT(*) as n FROM applications WHERE status = 'Ghosted'",
        one=True
    )
    tier_summary = {r['ghost_risk_tier']: r['n'] for r in counts}
    return jsonify({
        'message': 'Ghost check complete',
        'total_ghosted': ghosted['n'],
        'tiers': tier_summary,
    })


@app.route('/api/applications/<int:app_id>/restore', methods=['PUT'])
def api_restore_application(app_id):
    """Restore a ghosted application back to active when employer comes back."""
    d = request.json
    new_status = d.get('status', 'Applied')
    notes_append = d.get('note', '')

    row = query('SELECT * FROM applications WHERE id = %s', (app_id,), one=True)
    if not row:
        return jsonify({'error': 'Not found'}), 404

    existing_notes = row.get('notes') or ''
    timestamp = datetime.now().strftime('%d/%m/%Y')
    new_notes = f"{existing_notes}\n[{timestamp}] Restored from No Response: {notes_append}".strip()

    execute(
        """UPDATE applications
           SET status = %s, ghost_score = 0, outcome_date = NULL, notes = %s
           WHERE id = %s""",
        (new_status, new_notes, app_id)
    )
    row = query('SELECT * FROM applications WHERE id = %s', (app_id,), one=True)
    return jsonify(serialise(row))



if __name__ == '__main__':
    run_migrations()
    # Apply ghost tiers to all existing records immediately on startup
    try:
        run_ghost_check()
        print('Startup ghost check complete')
    except Exception as e:
        print(f'Startup ghost check error: {e}')
    t = threading.Thread(target=ghost_check_loop, daemon=True)
    t.start()
    app.run(
        debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true',
        host=os.getenv('FLASK_HOST', ''),
        port=int(os.getenv('FLASK_PORT', <port>))
    )
