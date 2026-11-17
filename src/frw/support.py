"""Offline help content and safe practice fixtures; no profile or network access."""
from __future__ import annotations

import copy
import html
import json
import platform
import re
from pathlib import Path
from . import AUTHOR, PROJECT_URL, SOURCE_LICENSE, __version__
from .config import DEFAULT


def topics():
    return json.loads((Path(__file__).with_name('data') / 'help.json').read_text(encoding='utf-8'))


def search_topics(query='', category='All topics'):
    words = re.findall(r'\w+', query.casefold())
    results = []
    for topic in topics():
        if category != 'All topics' and topic['category'] != category:
            continue
        haystack = json.dumps(topic, ensure_ascii=False).casefold()
        if all(word in haystack for word in words):
            score = sum(3 for word in words if word in topic['title'].casefold())
            results.append((score, topic))
    return [topic for score, topic in sorted(results, key=lambda pair: -pair[0])]


def article_html(topic):
    esc = html.escape
    parts = [f'<p style="color:#79dbc8">{esc(topic["category"].upper())} · FLIGHTRADAR24 {__version__}</p>',
             f'<h1>{esc(topic["title"])}</h1>', f'<p>{esc(topic["summary"])}</p>']
    for section in topic['sections']:
        parts.append(f'<h2>{esc(section["heading"])}</h2>')
        for paragraph in section.get('paragraphs', []):
            parts.append(f'<p>{esc(paragraph)}</p>')
        if section.get('steps'):
            parts.append('<ol>' + ''.join(f'<li>{esc(step)}</li>' for step in section['steps']) + '</ol>')
        if section.get('fields'):
            parts.append('<table width="100%" cellspacing="0" cellpadding="8">')
            for name, value, meaning in section['fields']:
                parts.append(f'<tr><td><b>{esc(name)}</b></td><td>{esc(value)}</td><td>{esc(meaning)}</td></tr>')
            parts.append('</table>')
    by_id = {item['id']: item for item in topics()}
    if topic.get('related'):
        parts.append('<h2>Related guides</h2>')
        parts.extend(f'<p><a href="help:{key}">{esc(by_id[key]["title"])}</a></p>' for key in topic['related'])
    return ''.join(parts)


def guide_markdown(selected=None):
    chosen = selected or topics()
    lines = [f'# FlightRadar24 user guide', '', f'Version {__version__} · {AUTHOR}', '',
             'All example aircraft, coordinates and delivery addresses are dummy data. Practice never changes your live profile.', '']
    if selected is None:
        lines += [f'- [{t["title"]}](#{t["id"]})' for t in chosen] + ['']
    for topic in chosen:
        lines += [f'<a id="{topic["id"]}"></a>', f'## {topic["title"]}', '', topic['summary'], '']
        for section in topic['sections']:
            lines += [f'### {section["heading"]}', '']
            for paragraph in section.get('paragraphs', []):
                lines += [paragraph, '']
            for n, step in enumerate(section.get('steps', []), 1):
                lines.append(f'{n}. {step}')
            if section.get('steps'):
                lines.append('')
            if section.get('fields'):
                lines += ['| Setting | Example / value | Meaning |', '| --- | --- | --- |']
                lines += ['| ' + ' | '.join(cell.replace('|', '\\|') for cell in row) + ' |' for row in section['fields']]
                lines.append('')
    return '\n'.join(lines)


def support_summary():
    # Deliberate allowlist: no usernames, paths, hostnames, aircraft, location,
    # settings, credentials, logs or automatic submission.
    return (f'FlightRadar24 {__version__}\nAuthor: {AUTHOR}\nSource licence: {SOURCE_LICENSE}\n'
            f'OS family: {platform.system()}\nArchitecture: {platform.machine()}\n'
            f'Project: {PROJECT_URL}\n\nWhat I was trying to do:\n\nSteps to reproduce:\n'
            '\nExpected result:\n\nActual result:\n\n'
            'Remove personal data and secrets before posting. Diagnostics are optional and should be reviewed.\n')


def example_receiver():
    # Arbitrary point in the Atlantic, deliberately unrelated to any user's antenna.
    return {**copy.deepcopy(DEFAULT), 'latitude': 0.0, 'longitude': -30.0,
            'altitude_ft': 100.0, 'feed_enabled': False, 'autostart_receiver': False}


def example_aircraft():
    base = dict(altitude=6500, groundspeed=145, track=75, vertical_rate=320,
                squawk='1200', latitude=.1, longitude=-30.1, seen=0, seen_pos=0,
                position_valid=True, on_ground=False, data_source='ADS-B', wake_vortex='Light',
                field_ages={'altitude':0, 'groundspeed':0, 'latitude':0, 'longitude':0})
    return [{**copy.deepcopy(base), 'icao':'A00001', 'callsign':'DEMO101'},
            {**copy.deepcopy(base), 'icao':'A00002', 'callsign':'DEMO202', 'altitude':18000, 'groundspeed':310},
            {**copy.deepcopy(base), 'icao':'A00003', 'callsign':'SAMPLE3', 'altitude':None, 'groundspeed':None}]
