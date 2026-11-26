"""Native filters, alert rules, statistics and storage controls."""
from __future__ import annotations
import copy
import csv
import datetime as dt
import json
import math
import time
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea, QSpinBox, QDoubleSpinBox, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QToolTip)
from .features import FEATURE_DEFAULT, FILTER_DEFAULT, CHANNELS, validate_filter, validate_features
CHANNEL_NAMES={'in_app':'In-app','sound':'Sound','desktop':'Desktop','email':'Email','webhook':'Webhook / phone'}

def label(text):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    return result

def button(text, callback):
    result = QPushButton(text)
    result.clicked.connect(lambda checked=False: callback())
    return result

def clock(timestamp):
    return dt.datetime.fromtimestamp(timestamp).astimezone().strftime('%d %b %H:%M:%S') if timestamp else '—'

def display(value):
    if value is None:
        return '—'
    return f'{value:,.1f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)

def table(headers):
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    result.verticalHeader().hide()
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    result.horizontalHeader().setStretchLastSection(True)
    result.setAlternatingRowColors(True)
    return result

def fill_table(target, rows, fields):
    selected = target.currentRow()
    target.setUpdatesEnabled(False)
    target.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, field in enumerate(fields):
            value = row.get(field)
            if field in ('first', 'last', 'time'):
                value = clock(value)
            item = QTableWidgetItem(display(value))
            item.setToolTip(item.text())
            target.setItem(i, j, item)
    if 0 <= selected < len(rows):
        target.selectRow(selected)
    target.setUpdatesEnabled(True)

def describe(criteria):
    parts = []
    for key, title in [('callsigns', 'Callsign'), ('icaos', 'ICAO'), ('squawks', 'Squawk'), ('category', 'Emitter')]:
        if criteria.get(key):
            parts.append(title+': '+criteria[key])
    for key, title in [('altitude_min','Altitude ≥'),('altitude_max','Altitude ≤'),('speed_min','Speed ≥'),('speed_max','Speed ≤'),('distance_max','Distance ≤')]:
        if criteria.get(key) is not None:
            parts.append(title+' '+str(criteria[key])+(' ft' if key.startswith('altitude') else ' kt' if key.startswith('speed') else ' NM'))
    for key in ('source','state'):
        if criteria.get(key,'any')!='any':
            parts.append(criteria[key])
    if criteria.get('positioned'):
        parts.append('Position received')
    if criteria.get('emergency'):
        parts.append('Emergency code/state')
    return ' · '.join(parts) or 'All received aircraft'

class FeatureModel(QObject):
    changed = Signal(dict)
    notice = Signal(str, bool)
    localTest = Signal()

    def __init__(self, invoke, parent=None):
        super().__init__(parent)
        self.invoke = invoke
        self.value = copy.deepcopy(FEATURE_DEFAULT)
        self.stored = {}
        self.pending = False
        self.loaded = False

    def load(self):
        def done(data, error=None):
            if error:
                self.notice.emit('Feature preferences unavailable: '+str(error), True)
                return
            self.value, self.stored = data['settings'], data['stored_secrets']
            self.loaded = True
            self.changed.emit(copy.deepcopy(self.value))
        self.invoke('/api/features', None, done)

    def save(self, updates, secrets=None, on_saved=None):
        if self.pending or not self.loaded:
            self.notice.emit('Wait for the current preferences operation to finish.', True)
            return
        candidate = copy.deepcopy(self.value)
        candidate.update(copy.deepcopy(updates))
        try:
            candidate = validate_features(candidate)
        except (ValueError, KeyError, TypeError) as error:
            self.notice.emit(str(error), True)
            return
        self.pending = True
        def done(data, error=None):
            self.pending = False
            if error:
                self.notice.emit('Could not save preferences: '+str(error), True)
                return
            self.value, self.stored = data['settings'], data['stored_secrets']
            self.changed.emit(copy.deepcopy(self.value))
            if on_saved:
                on_saved()
            self.notice.emit('Preferences saved. Reception and uploading continue independently of display filters.', False)
        payload = {'settings': candidate}
        if secrets is not None:
            payload['secrets'] = secrets
        self.invoke('/api/features', payload, done)

class FilterEditor(QWidget):
    def __init__(self, criteria=None, parent=None):
        super().__init__(parent)
        self.controls = {}
        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key, title, hint in [('callsigns','Callsigns','QFA*, VOZ123 · comma-separated; * at end'),
            ('icaos','ICAO24 addresses','7C0001, 7C0002 · exact 6 hex digits'),
            ('squawks','Squawks','1200, 2000, 7700 · exact 4 octal digits'),
            ('altitude_min','Minimum altitude (ft)','Any'),('altitude_max','Maximum altitude (ft)','Any'),
            ('speed_min','Minimum ground speed (kt)','Any'),('speed_max','Maximum ground speed (kt)','Any'),
            ('distance_max','Within antenna range (NM)','Any · antenna coordinates required'),
            ('category','Broadcast emitter category','Text contains, e.g. rotorcraft or light')]:
            control = QLineEdit()
            control.setPlaceholderText(hint)
            control.setMaxLength(512 if key in ('callsigns','icaos','squawks') else 80)
            control.setAccessibleName(title)
            control.setObjectName('filter_'+key)
            self.controls[key]=control
            form.addRow(title, control)
        for key, title, choices in [('source','Data source',['any','ADS-B','Extended squitter (DF18)','Mode S','Mode S / Comm-B','SBS/BaseStation input']),
                                      ('state','Flight state',['any','airborne','ground'])]:
            control=QComboBox()
            for choice in choices:
                control.addItem('Any' if choice=='any' else choice,choice)
            control.setAccessibleName(title)
            self.controls[key]=control
            form.addRow(title,control)
        for key,title in [('positioned','Only aircraft with a fresh received position'),('emergency','Emergency squawk (7500/7600/7700) or broadcast emergency state')]:
            control=QCheckBox(title)
            self.controls[key]=control
            form.addRow(control)
        form.addRow(label('All chosen fields must match. Comma-separated codes match any listed value. Missing or expired measurements do not match numeric limits. Airline, registration, model, route and aircraft age are unavailable from the receiver.'))
        self.set_value(criteria or FILTER_DEFAULT)

    def set_value(self, criteria):
        for key, control in self.controls.items():
            value=criteria.get(key,FILTER_DEFAULT[key])
            if isinstance(control,QComboBox):
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control,QCheckBox):
                control.setChecked(value)
            else:
                control.setText('' if value is None else str(value))

    def value(self):
        result={}
        for key, control in self.controls.items():
            if isinstance(control,QComboBox):
                value=control.currentData()
            elif isinstance(control,QCheckBox):
                value=control.isChecked()
            else:
                value=control.text().strip()
                if key in ('altitude_min','altitude_max','speed_min','speed_max','distance_max'):
                    value=float(value) if value else None
            result[key]=value
        return validate_filter(result)

class FilterDialog(QDialog):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Airspace display filters')
        self.resize(600,700)
        self.model=model
        layout=QVBoxLayout(self)
        self.editor=FilterEditor(model.value['filter'])
        layout.addWidget(self.editor)
        self.preset=QLineEdit()
        self.preset.setPlaceholderText('Optional preset name, e.g. Low aircraft nearby')
        self.preset.setAccessibleName('Save filter preset name')
        self.preset.setMaxLength(60)
        layout.addWidget(self.preset)
        presets=QHBoxLayout();self.saved_presets=QComboBox();self.saved_presets.setAccessibleName('Saved filter presets')
        self.saved_presets.addItem('Choose a saved preset to remove','')
        for entry in model.value['presets']:self.saved_presets.addItem(entry['name'],entry['name'])
        presets.addWidget(self.saved_presets,1);presets.addWidget(button('Remove saved preset',self.remove_preset));layout.addLayout(presets)
        self.error=label('')
        layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        try:
            criteria=self.editor.value()
        except ValueError as error:
            self.error.setText(str(error)); return
        presets=copy.deepcopy(self.model.value['presets'])
        if self.preset.text().strip():
            presets=[p for p in presets if p['name']!=self.preset.text().strip()]
            presets.append({'name':self.preset.text().strip(),'filter':criteria})
        self.model.save({'filter':criteria,'filter_enabled':True,'presets':presets})
        self.accept()

    def remove_preset(self):
        name=self.saved_presets.currentData()
        if name:
            self.model.save({'presets':[p for p in self.model.value['presets'] if p['name']!=name]})
            self.saved_presets.removeItem(self.saved_presets.currentIndex())

class RuleDialog(QDialog):
    def __init__(self, rule=None, icao='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Aircraft alert rule')
        self.resize(640,860)
        self.rule=copy.deepcopy(rule or {'id':uuid.uuid4().hex,'name':'Aircraft watch','enabled':True,
            'filter':{**FILTER_DEFAULT,'icaos':icao},'channels':['in_app','sound','desktop'],
            'cooldown_seconds':900,'include_playback':False})
        layout=QVBoxLayout(self)
        top=QFormLayout()
        self.name=QLineEdit(self.rule['name']);self.name.setMaxLength(80)
        self.enabled=QCheckBox('Rule enabled');self.enabled.setChecked(self.rule['enabled'])
        top.addRow('Rule name',self.name);top.addRow(self.enabled)
        layout.addLayout(top)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        self.editor=FilterEditor(self.rule['filter'])
        scroll.setWidget(self.editor);layout.addWidget(scroll,1)
        channels=QHBoxLayout();self.channels={}
        for key,title in [('in_app','In-app history'),('sound','Sound'),('desktop','Desktop'),('email','Email'),('webhook','Webhook / phone')]:
            checkbox=QCheckBox(title);checkbox.setChecked(key in self.rule['channels'])
            self.channels[key]=checkbox;channels.addWidget(checkbox)
        layout.addLayout(channels)
        form=QFormLayout();self.cooldown=QSpinBox();self.cooldown.setRange(30,86400);self.cooldown.setValue(self.rule['cooldown_seconds']);self.cooldown.setSuffix(' seconds')
        form.addRow('Minimum repeat interval',self.cooldown)
        self.playback=QCheckBox('Also alert for recorded playback (clearly labelled)');self.playback.setChecked(self.rule['include_playback'])
        form.addRow(self.playback);layout.addLayout(form)
        layout.addWidget(label('Alerts fire when an aircraft first matches or re-enters after leaving/losing reception. A maximum of 20 alerts per minute applies across all rules. Email and webhook also require delivery settings.'))
        self.error=label('');layout.addWidget(self.error)
        controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.save);controls.rejected.connect(self.reject);layout.addWidget(controls)

    def save(self):
        try:
            self.rule.update(name=self.name.text().strip(),enabled=self.enabled.isChecked(),filter=self.editor.value(),
                channels=[key for key,c in self.channels.items() if c.isChecked()],cooldown_seconds=self.cooldown.value(),include_playback=self.playback.isChecked())
            validate_features({'rules':[self.rule]})
        except ValueError as error:
            self.error.setText(str(error));return
        self.accept()

class AlertsPage(QWidget):
    def __init__(self,model,open_settings,parent=None):
        super().__init__(parent)
        self.model=model
        layout=QVBoxLayout(self)
        header=QHBoxLayout()
        self.master=QCheckBox('Enable aircraft alerts')
        self.master.clicked.connect(lambda checked:model.save({'alerts_enabled':checked}))
        header.addWidget(self.master);header.addStretch(1)
        header.addWidget(button('Delivery & quiet hours',open_settings))
        header.addWidget(button('Test local alert',lambda:model.localTest.emit()))
        layout.addLayout(header)
        layout.addWidget(label('Watch specific aircraft or combine callsign, squawk, altitude, speed, range and other received fields. Alerts use all received traffic, including aircraft hidden by display filters. The application and receiver must be running.'))
        self.rules=table(['Enabled','Rule','Matches','Channels','Cooldown'])
        self.rules.cellDoubleClicked.connect(lambda r,c:self.edit())
        layout.addWidget(self.rules,2)
        actions=QHBoxLayout()
        for title,callback in [('Add rule',self.add),('Edit selected',self.edit),('Remove selected',self.remove),('Add emergency preset',self.emergency)]:
            actions.addWidget(button(title,callback))
        actions.addStretch(1);layout.addLayout(actions)
        layout.addWidget(label('Recent alerts · expand the message by selecting a row. Delivery status confirms local/server acceptance, not whether a person read it.'))
        self.recent=table(['Received','Rule','Aircraft','Data','Delivery'])
        self.recent.itemSelectionChanged.connect(self.show_event)
        layout.addWidget(self.recent,2)
        self.message=label('No alerts in this app session. Retained alerts are available in Statistics → Alert history.')
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)
        self.health=label('');layout.addWidget(self.health)
        self.events=[]
        model.changed.connect(self.update_config)

    def update_config(self,config):
        self.master.setChecked(config['alerts_enabled'])
        rows=[dict(enabled='On' if r['enabled'] else 'Off',name=r['name'],matches=describe(r['filter']),channels=', '.join(CHANNEL_NAMES[c] for c in r['channels']),cooldown=f"{r['cooldown_seconds']} s") for r in config['rules']]
        fill_table(self.rules,rows,['enabled','name','matches','channels','cooldown'])

    def update_status(self,status):
        health=status.get('activity') or {}
        events=list(reversed(health.get('events',[])))
        if events!=self.events:
            self.events=events
            rendered=[{**event,'aircraft':event.get('callsign') or event['icao'],
                'data':'Recorded playback' if event['mode']=='playback' else 'Test' if event['mode']=='test' else 'Live'} for event in events]
            fill_table(self.recent,rendered,['time','rule','aircraft','data','delivery'])
            if events and self.recent.currentRow()<0:
                self.recent.selectRow(0)
        self.health.setText(' · '.join(filter(None,[health.get('error',''),f"History reports dropped: {health.get('dropped_reports',0)}",f"Rate-limited alerts: {health.get('suppressed_alerts',0)}",f"Delivery queue drops: {health.get('delivery_dropped',0)}"])))

    def show_event(self):
        index=self.recent.currentRow()
        if 0<=index<len(self.events):
            self.message.setText(self.events[index]['body']+'\n'+self.events[index]['delivery'])

    def add(self,icao=''):
        dialog=RuleDialog(icao=icao,parent=self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.model.save({'rules':self.model.value['rules']+[dialog.rule]})

    def edit(self):
        index=self.rules.currentRow()
        if not 0<=index<len(self.model.value['rules']):
            return
        dialog=RuleDialog(self.model.value['rules'][index],parent=self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            rules=copy.deepcopy(self.model.value['rules']);rules[index]=dialog.rule
            self.model.save({'rules':rules})

    def remove(self):
        index=self.rules.currentRow()
        if 0<=index<len(self.model.value['rules']):
            rules=copy.deepcopy(self.model.value['rules']);rules.pop(index)
            self.model.save({'rules':rules})

    def emergency(self):
        rule={'id':uuid.uuid4().hex,'name':'Emergency broadcast','enabled':True,
              'filter':{**FILTER_DEFAULT,'emergency':True},'channels':['in_app','sound','desktop'],
              'cooldown_seconds':900,'include_playback':False}
        self.model.save({'rules':self.model.value['rules']+[rule]})

class BarChart(QWidget):
    def __init__(self,title,unit,parent=None):
        super().__init__(parent)
        self.title,self.unit=title,unit
        self.points=[]
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setAccessibleName(title)

    def set_points(self,points):
        self.points=points
        self.setAccessibleDescription(self.unit+'. '+('; '.join(f'{x}: {v:g}' for x,v in points) if points else 'No stored observations in this period.'))
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(),QColor('#172b3b'))
        painter.setPen(QColor('#e8edf4'));painter.drawText(14,24,self.title+' · '+self.unit)
        if not self.points:
            painter.setPen(QColor('#a8b9cb'));painter.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'No stored observations in this period')
            return
        left,top,width,height=55,45,max(1,self.width()-75),max(1,self.height()-82)
        maximum=max(3,math.ceil(max(value for _,value in self.points)/3)*3)
        painter.setPen(QColor('#3b5367'))
        for step in range(4):
            y=top+height-step*height/3
            painter.drawLine(left,int(y),left+width,int(y))
            painter.setPen(QColor('#a8b9cb'));painter.drawText(4,int(y)+4,f'{maximum*step/3:,.0f}');painter.setPen(QColor('#3b5367'))
        stride=width/len(self.points)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#79dbc8'))
        for i,(name,value) in enumerate(self.points):
            bar_height=value/maximum*height
            painter.drawRect(int(left+i*stride+2),int(top+height-bar_height),max(1,int(stride-4)),int(bar_height))
        painter.setPen(QColor('#a8b9cb'))
        for i,(name,value) in enumerate(self.points):
            if i%(1 if len(self.points)<=8 else math.ceil(len(self.points)/6))==0:
                painter.drawText(int(left+i*stride),top+height+22,name[:10])

    def mouseMoveEvent(self,event):
        if self.points and 55<=event.position().x()<self.width()-20:
            index=min(len(self.points)-1,int((event.position().x()-55)/(self.width()-75)*len(self.points)))
            name,value=self.points[index]
            QToolTip.showText(event.globalPosition().toPoint(),f'{name}: {value:,.1f} {self.unit}',self)

class StatisticsPage(QWidget):
    def __init__(self,model,parent=None):
        super().__init__(parent)
        self.model=model;self.report={};self.pending=False;self.closed=False
        layout=QVBoxLayout(self)
        row=QHBoxLayout()
        self.period=QComboBox()
        for text,hours in [('Last hour',1),('Last 24 hours',24),('Last 7 days',168),('Last 30 days',720),('All retained',87600)]:
            self.period.addItem(text,hours)
        self.period.setCurrentIndex(1);self.period.setAccessibleName('Statistics period')
        self.mode=QComboBox();self.mode.addItem('Live reception','live');self.mode.addItem('Recorded playback','playback');self.mode.setAccessibleName('Statistics data source')
        row.addWidget(self.period);row.addWidget(self.mode);row.addStretch(1)
        row.addWidget(button('Refresh',self.refresh));row.addWidget(button('Export this view CSV…',self.export))
        layout.addLayout(row)
        self.status=label('Loading retained observations…');layout.addWidget(self.status)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();overview=QVBoxLayout(body);scroll.setWidget(body)
        self.cards=label('');self.cards.setObjectName('metrics');overview.addWidget(self.cards)
        self.detail=label('');overview.addWidget(self.detail)
        chartrow=QHBoxLayout();self.traffic=BarChart('Decoded traffic','messages');self.aircraft=BarChart('Aircraft observed','unique addresses')
        chartrow.addWidget(self.traffic);chartrow.addWidget(self.aircraft);overview.addLayout(chartrow)
        self.coverage=BarChart('Maximum received range by bearing','NM');overview.addWidget(self.coverage)
        self.sources=table(['Received source','Decoded messages']);self.sources.setMinimumHeight(160);overview.addWidget(self.sources)
        overview.addWidget(label('Counts describe only this receiver’s coverage. Unique aircraft means distinct ICAO24 addresses. An encounter is an address seen with no gap of 20 minutes. It is not a confirmed flight or airport movement. Empty chart intervals mean no stored reports, including when the receiver was off. Period boundaries are rounded to minutes.'))
        self.tabs.addTab(scroll,'Overview')
        self.encounters=table(['ICAO24','Callsign','First received','Last received','Messages','Max altitude ft','Max speed kt','Closest NM','Source'])
        self.tabs.addTab(self.encounters,'Aircraft encounters')
        airportbody=QWidget();airportlayout=QVBoxLayout(airportbody)
        airportlayout.addWidget(label('Aircraft encounters observed within each configured airport radius. These counts do not establish origin, destination, arrival or departure. Add airports worldwide with their coordinates below.'))
        self.airports=table(['Airport','Name','Radius NM','Encounters nearby','Closest NM']);airportlayout.addWidget(self.airports)
        airportlayout.addWidget(button('Add airport to monitor…',self.add_airport))
        self.tabs.addTab(airportbody,'Airports')
        self.alerts=table(['Received','Rule','ICAO24','Callsign','Delivery','Message'])
        self.tabs.addTab(self.alerts,'Alert history')
        self.period.currentIndexChanged.connect(self.refresh);self.mode.currentIndexChanged.connect(self.refresh)
        self.timer=QTimer(self);self.timer.setInterval(5000);self.timer.timeout.connect(self.refresh);self.timer.start()
        model.changed.connect(lambda _:self.refresh())

    def showEvent(self,event):
        super().showEvent(event);self.refresh()

    def refresh(self):
        if self.closed or self.pending or not self.isVisible():
            return
        self.pending=True
        hours,mode=self.period.currentData(),self.mode.currentData()
        def done(data,error=None):
            self.pending=False
            if self.closed:
                return
            if error:
                self.status.setText('History unavailable: '+str(error));return
            if hours!=self.period.currentData() or mode!=self.mode.currentData():
                self.refresh();return
            self.report=data;self.render()
        self.model.invoke(f'/api/statistics?hours={hours}&mode={mode}',None,done)

    def render(self):
        report=self.report;summary=report['summary'];health=report.get('health') or {}
        self.cards.setText(f"{summary['messages']:,} decoded messages     {summary['aircraft']:,} aircraft     {summary['encounters']:,} encounters")
        self.detail.setText(f"Input frames: {summary['frames']:,} · Rejected decode attempts: {summary['rejected']:,} · Maximum altitude: {display(summary['max_alt'])} ft · Maximum ground speed: {display(summary['max_speed'])} kt · Closest: {display(summary['closest_nm'])} NM · Vendor aircraft reports sent: {summary['upload_aircraft_reports']:,}")
        self.status.setText(f"{'Recording history' if report['history_enabled'] else 'History recording off'} · {report['mode']} · {report['storage_bytes']/1048576:.1f} / {report['storage_limit_bytes']/1048576:.0f} MiB history budget · Retention {report['retention_days']} days · Earliest retained: {clock(report['oldest'])} · Updated {clock(report['generated'])}" + (' · '+health['error'] if health.get('error') else ''))
        points=report['traffic']
        names=[dt.datetime.fromtimestamp(p['t']).strftime('%d %b' if report['bucket_seconds']==86400 else '%H:%M') for p in points]
        self.traffic.set_points([(name,p['messages']) for name,p in zip(names,points)])
        self.aircraft.set_points([(name,p['aircraft']) for name,p in zip(names,points)])
        sectors={r['sector']:r['distance'] for r in report['coverage']}
        self.coverage.set_points([(name,sectors.get(i,0)) for i,name in enumerate(['N','NE','E','SE','S','SW','W','NW'])] if sectors else [])
        fill_table(self.sources,report['sources'],['source','messages'])
        fill_table(self.encounters,report['encounters'],['icao','callsign','first','last','messages','max_alt','max_speed','min_distance','source'])
        fill_table(self.airports,report['airports'],['code','name','radius','encounters','closest_nm'])
        fill_table(self.alerts,report['alerts'],['time','rule','icao','callsign','delivery','body'])

    def add_airport(self):
        dialog=AirportDialog(parent=self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            airports=[a for a in self.model.value['airports'] if a['code']!=dialog.airport['code']]+[dialog.airport]
            self.model.save({'airports':airports})

    def export(self):
        kind=['traffic','encounters','airports','alerts'][self.tabs.currentIndex()]
        rows=self.report.get(kind,[])
        if not rows:
            self.model.notice.emit('No retained rows in this view and period.',False);return
        path,_=QFileDialog.getSaveFileName(self,'Export retained '+kind,kind+'.csv','CSV files (*.csv)')
        if not path:
            return
        try:
            with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader()
                for row in rows:
                    writer.writerow({key: "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value for key,value in row.items()})
            self.model.notice.emit('Retained '+kind+' exported.',False)
        except OSError as error:
            self.model.notice.emit(str(error),True)

class AirportDialog(QDialog):
    def __init__(self,airport=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Airport proximity reference')
        self.resize(580,430)
        layout=QVBoxLayout(self);self.fields={};form=QFormLayout()
        self.search=QLineEdit();self.search.setPlaceholderText('Search bundled worldwide airports by code, name or city')
        self.search.setAccessibleName('Search airport reference')
        layout.addWidget(self.search);self.results=QListWidget();self.results.setMaximumHeight(140);layout.addWidget(self.results)
        self.catalog=[]
        catalog=Path(__file__).parent/'data/airports.json'
        if catalog.exists():
            self.catalog=json.loads(catalog.read_text('utf-8'))
        self.search.textChanged.connect(self.search_airports);self.results.itemClicked.connect(self.choose)
        for key,title in [('code','ICAO / local code'),('name','Airport name'),('latitude','Latitude (decimal degrees)'),('longitude','Longitude (decimal degrees)'),('radius_nm','Monitoring radius (NM)')]:
            control=QLineEdit();control.setAccessibleName(title);self.fields[key]=control
            if airport:
                control.setText(str(airport[key]))
            elif key=='radius_nm':
                control.setText('10')
            form.addRow(title,control)
        layout.addLayout(form)
        layout.addWidget(label('Worldwide reference coordinates or manual entries. Radius observations are not confirmed airport movements. Airport data: OurAirports, public domain.'))
        self.error=label('');layout.addWidget(self.error)
        controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.save);controls.rejected.connect(self.reject);layout.addWidget(controls)
        self.airport=None

    def search_airports(self,query):
        self.results.clear()
        query=query.casefold().strip()
        if len(query)<2:
            return
        self.found=[a for a in self.catalog if query in ' '.join(str(v) for v in a.values()).casefold()][:40]
        for airport in self.found:
            self.results.addItem(airport['code']+' · '+airport['name'])

    def choose(self,item):
        airport=self.found[self.results.row(item)]
        for key in ('code','name','latitude','longitude'):
            self.fields[key].setText(str(airport[key]))

    def save(self):
        try:
            value={key:control.text().strip() for key,control in self.fields.items()}
            for key in ('latitude','longitude','radius_nm'):
                value[key]=float(value[key])
            self.airport=validate_features({'airports':[value]})['airports'][0]
        except ValueError as error:
            self.error.setText(str(error));return
        self.accept()

class FeaturePreferences(QScrollArea):
    def __init__(self,model,parent=None):
        super().__init__(parent)
        self.model=model;self.initialized=False;self.fields={};self.secrets={}
        self.setWidgetResizable(True)
        body=QWidget();layout=QVBoxLayout(body);self.setWidget(body)
        layout.setContentsMargins(18,16,18,18)
        self.feedback=label('');layout.addWidget(self.feedback)
        model.notice.connect(lambda message,error:self.feedback.setText(message))
        displaybox=QGroupBox('Display & units');displayform=QFormLayout(displaybox)
        self.display_units=QComboBox()
        for title,key in [('Aviation — feet, knots, nautical miles','aviation'),('Metric — metres, km/h, kilometres','metric')]:self.display_units.addItem(title,key)
        displayform.addRow('Units',self.display_units)
        self.display_labels=QComboBox()
        for title,key in [('Callsign only','callsign'),('Callsign, altitude and speed','detailed'),('No labels','none'),('Custom fields','custom')]:self.display_labels.addItem(title,key)
        displayform.addRow('Map labels',self.display_labels)
        self.label_fields={}
        label_grid=QGridLayout()
        for i,(key,title) in enumerate([('callsign','Callsign'),('icao','ICAO'),('altitude','Altitude'),('groundspeed','Ground speed'),('track','Track'),('vertical_rate','Vertical rate'),('squawk','Squawk'),('distance','Distance'),('seen','Last message age')]):
            control=QCheckBox(title);self.label_fields[key]=control;label_grid.addWidget(control,i//2,i%2)
            control.clicked.connect(lambda _checked: self.display_labels.setCurrentIndex(self.display_labels.findData('custom')))
            control.setToolTip('Selecting a field switches map labels to Custom fields.')
        displayform.addRow('Custom label fields',label_grid)
        self.display_hover=QCheckBox('Show aircraft information on hover');displayform.addRow(self.display_hover)
        self.display_conflict=QCheckBox('Show estimated conflict alerts');displayform.addRow(self.display_conflict)
        self.display_conflict_flash=QCheckBox('Flash estimated conflicts');displayform.addRow(self.display_conflict_flash)
        displayform.addRow(label('Yellow/red estimates need fresh position, pressure altitude and motion data. They are not TCAS or navigation guidance. Height above terrain is unavailable.'))
        self.display_rings=QCheckBox('Show range rings around my antenna on the map');displayform.addRow(self.display_rings)
        self.display_follow=QCheckBox('Keep the selected aircraft centred on the map');displayform.addRow(self.display_follow)
        self.display_cardinals=QCheckBox('Show compass and N/E/S/W cardinal labels');displayform.addRow(self.display_cardinals)
        self.display_flash=QCheckBox('Flash emergency aircraft red on the map (off = steady red)');displayform.addRow(self.display_flash)
        self.display_north=QComboBox()
        for title,key in [('True north','true'),('Magnetic north (uses the variation below)','magnetic')]:self.display_north.addItem(title,key)
        displayform.addRow('Bearing & compass reference',self.display_north)
        self.display_variation=QDoubleSpinBox();self.display_variation.setRange(-30,30);self.display_variation.setDecimals(1);self.display_variation.setSingleStep(0.5);self.display_variation.setSuffix('° (east positive)')
        displayform.addRow('Magnetic variation at my antenna',self.display_variation)
        displayform.addRow(button('Save display settings',self.save_display))
        displayform.addRow(label('Display choices change presentation only. Reception, uploading, filters, alerts and recorded history always use the values as broadcast. Altitude at or above 10,000 ft is also shown as an approximate flight level (FL). Metric values are converted from the broadcast feet/knots; nothing is re-measured. Distance and bearing need your antenna latitude/longitude in receiver settings. The background map is drawn with true north up; choosing Magnetic north rotates the compass, ring cardinals and table bearings by the variation you enter from a current chart for your location — the app does not calculate variation itself. Range rings are dotted every 50 units from your antenna.'))
        layout.addWidget(displaybox)
        storage=QGroupBox('History & disk budget');form=QFormLayout(storage)
        self.history=QCheckBox('Collect structured reception statistics and sampled observations')
        form.addRow(self.history)
        for key,title,limits,suffix in [('retention_days','Keep history for',(1,3650),' days'),('storage_mb','History database + journal budget',(8,4096),' MiB'),
            ('sample_seconds','Sample each aircraft at most once every',(5,300),' seconds'),('log_max_mb','Total diagnostic log budget',(1,1024),' MiB'),('log_days','Keep rotated logs for',(1,365),' days')]:
            control=QSpinBox();control.setRange(*limits);control.setSuffix(suffix);self.fields[key]=control;form.addRow(title,control)
        form.addRow(label('The oldest history is removed when its age or size limit is reached. Part of the history budget is reserved for safe database writes. History is separate from logs; disabling file logging does not disable statistics. The map tile cache has its own fixed 64 MiB limit. Exports and manual backups are outside these budgets.'))
        layout.addWidget(storage)
        quiet=QGroupBox('Quiet hours · local computer time');quietform=QFormLayout(quiet)
        for key,title in [('quiet_start','Silence from (HH:MM)'),('quiet_end','Until (HH:MM)')]:
            control=QLineEdit();control.setPlaceholderText('Leave both blank for no quiet hours');self.fields[key]=control;quietform.addRow(title,control)
        quietform.addRow(label('During quiet hours, matching events are saved in the app without sound, desktop, email or webhook delivery.'))
        layout.addWidget(quiet)
        emailbox=QGroupBox('Email · your own SMTP account');emailform=QFormLayout(emailbox)
        self.email_enabled=QCheckBox('Enable email delivery');emailform.addRow(self.email_enabled)
        for key,title in [('smtp_host','SMTP server'),('smtp_port','SMTP port'),('smtp_user','SMTP username'),('smtp_from','From address'),('email_to','Recipient address')]:
            control=QSpinBox() if key=='smtp_port' else QLineEdit()
            if key=='smtp_port':control.setRange(1,65535)
            self.fields[key]=control;emailform.addRow(title,control)
        self.tls=QComboBox();self.tls.addItem('STARTTLS (usually port 587)','starttls');self.tls.addItem('TLS from connection (usually 465)','ssl');emailform.addRow('Transport encryption',self.tls)
        password=QLineEdit();password.setEchoMode(QLineEdit.EchoMode.Password);password.setPlaceholderText('Leave blank to keep the stored password');self.secrets['smtp_password']=password
        emailform.addRow('SMTP / app password',password)
        emailform.addRow(label('This app requires password/app-password SMTP authentication over verified TLS. Providers requiring OAuth only need a compatible SMTP relay. Your provider may impose limits or charges; the app adds no email subscription.'))
        layout.addWidget(emailbox)
        webhook=QGroupBox('Free webhook / phone options');webform=QFormLayout(webhook)
        self.webhook_enabled=QCheckBox('Enable HTTPS webhook delivery');webform.addRow(self.webhook_enabled)
        self.webkind=QComboBox()
        for name,key in [('ntfy topic (phone push)','ntfy'),('Discord webhook','discord'),('Generic JSON webhook','json')]:self.webkind.addItem(name,key)
        webform.addRow('Service format',self.webkind)
        for key,title,hint in [('webhook_url','HTTPS topic / webhook URL','Leave blank to keep stored endpoint'),('webhook_token','Optional bearer token','Leave blank to keep stored token')]:
            control=QLineEdit();control.setEchoMode(QLineEdit.EchoMode.Password);control.setPlaceholderText(hint);self.secrets[key]=control;webform.addRow(title,control)
        webform.addRow(label('ntfy offers free phone subscriptions and self-hosting; Discord supports free channel webhooks. Service limits and mobile background behaviour vary. Subscribe to your topic on the receiving device. Public ntfy topics can be read by anyone who knows the name; use an authenticated/private topic for sensitive locations. Alerts send the displayed aircraft observations to your configured recipient/service.'))
        layout.addWidget(webhook)
        self.secret_state=label('');layout.addWidget(self.secret_state)
        actions=QHBoxLayout();actions.addWidget(button('Save storage & delivery settings',self.save));actions.addWidget(button('Test local sound & desktop',lambda:model.localTest.emit()));actions.addWidget(button('Send external test',self.external_test));actions.addStretch(1);layout.addLayout(actions)
        self.clear_secrets=QCheckBox('Remove stored alert password, URL and token on next save');layout.addWidget(self.clear_secrets)
        layout.addWidget(label('External test sends a clearly labelled synthetic aircraft message to the saved enabled email/webhook destinations. Save changes first. The app must stay running for monitoring and alerts.'))
        airportbox=QGroupBox('Airports to monitor');airportlayout=QVBoxLayout(airportbox)
        self.airport_table=table(['Code','Airport','Latitude','Longitude','Radius NM']);self.airport_table.setMinimumHeight(160);airportlayout.addWidget(self.airport_table)
        airportbuttons=QHBoxLayout();airportbuttons.addWidget(button('Add airport…',self.add_airport));airportbuttons.addWidget(button('Edit selected',self.edit_airport));airportbuttons.addWidget(button('Remove selected',self.remove_airport));airportbuttons.addStretch(1);airportlayout.addLayout(airportbuttons)
        layout.addWidget(airportbox);layout.addStretch(1)
        model.changed.connect(self.update_config)

    def update_config(self,config):
        if not self.initialized:
            self.initialized=True
            self.history.setChecked(config['history_enabled'])
            for key,control in self.fields.items():
                value=config.get(key,config['delivery'].get(key))
                control.setValue(value) if isinstance(control,QSpinBox) else control.setText(value)
            display=config.get('display') or {}
            self.display_units.setCurrentIndex(max(0,self.display_units.findData(display.get('units','aviation'))))
            self.display_labels.setCurrentIndex(max(0,self.display_labels.findData(display.get('map_labels','callsign'))))
            selected=set(display.get('map_label_fields',['callsign','altitude','groundspeed']))
            for key,control in self.label_fields.items():control.setChecked(key in selected)
            self.display_hover.setChecked(display.get('aircraft_hover',True));self.display_conflict.setChecked(display.get('conflict_enabled',True));self.display_conflict_flash.setChecked(display.get('conflict_flash',True))
            self.display_rings.setChecked(bool(display.get('range_rings',True)));self.display_follow.setChecked(bool(display.get('follow_selected',False)))
            self.display_cardinals.setChecked(bool(display.get('cardinals',True)));self.display_flash.setChecked(bool(display.get('emergency_flash',True)))
            self.display_north.setCurrentIndex(max(0,self.display_north.findData(display.get('north_reference','true'))))
            self.display_variation.setValue(float(display.get('magnetic_variation',0) or 0))
            self.email_enabled.setChecked(config['delivery']['email_enabled']);self.webhook_enabled.setChecked(config['delivery']['webhook_enabled'])
            self.tls.setCurrentIndex(self.tls.findData(config['delivery']['smtp_tls']));self.webkind.setCurrentIndex(self.webkind.findData(config['delivery']['webhook_kind']))
        fill_table(self.airport_table,config['airports'],['code','name','latitude','longitude','radius_nm'])
        self.secret_state.setText('Stored protected alert credentials: '+(', '.join(key.replace('_',' ') for key,value in self.model.stored.items() if value) or 'none'))

    def save(self):
        changes={key:(control.value() if isinstance(control,QSpinBox) else control.text().strip()) for key,control in self.fields.items() if key in FEATURE_DEFAULT}
        changes['history_enabled']=self.history.isChecked()
        changes['display']={'units':self.display_units.currentData(),'map_labels':self.display_labels.currentData(),'map_label_fields':[key for key,control in self.label_fields.items() if control.isChecked()],'aircraft_hover':self.display_hover.isChecked(),'conflict_enabled':self.display_conflict.isChecked(),'conflict_flash':self.display_conflict_flash.isChecked(),'range_rings':self.display_rings.isChecked(),'follow_selected':self.display_follow.isChecked(),'cardinals':self.display_cardinals.isChecked(),'emergency_flash':self.display_flash.isChecked(),'north_reference':self.display_north.currentData(),'magnetic_variation':self.display_variation.value()}
        delivery=copy.deepcopy(self.model.value['delivery'])
        delivery.update({key:(control.value() if isinstance(control,QSpinBox) else control.text().strip()) for key,control in self.fields.items() if key in delivery})
        delivery.update(email_enabled=self.email_enabled.isChecked(),webhook_enabled=self.webhook_enabled.isChecked(),smtp_tls=self.tls.currentData(),webhook_kind=self.webkind.currentData())
        changes['delivery']=delivery
        secrets={key:control.text() for key,control in self.secrets.items() if control.text()}
        if self.clear_secrets.isChecked():
            secrets={key:'' for key in self.secrets}
            changes['delivery']['email_enabled']=False
            changes['delivery']['webhook_enabled']=False
        def saved():
            for control in self.secrets.values():control.clear()
            self.email_enabled.setChecked(self.model.value['delivery']['email_enabled'])
            self.webhook_enabled.setChecked(self.model.value['delivery']['webhook_enabled'])
            self.clear_secrets.setChecked(False)
        self.model.save(changes,secrets or None,on_saved=saved)

    def save_display(self):
        display={'units':self.display_units.currentData(),'map_labels':self.display_labels.currentData(),
                 'map_label_fields':[key for key,control in self.label_fields.items() if control.isChecked()],
                 'aircraft_hover':self.display_hover.isChecked(),'conflict_enabled':self.display_conflict.isChecked(),
                 'conflict_flash':self.display_conflict_flash.isChecked(),'range_rings':self.display_rings.isChecked(),
                 'follow_selected':self.display_follow.isChecked(),'cardinals':self.display_cardinals.isChecked(),
                 'emergency_flash':self.display_flash.isChecked(),'north_reference':self.display_north.currentData(),
                 'magnetic_variation':self.display_variation.value()}
        self.model.save({'display':display})

    def external_test(self):
        self.feedback.setText('Sending a labelled external test using saved delivery settings…')
        def done(result,error=None):
            self.feedback.setText(str(error) if error else result['delivery'])
        self.model.invoke('/api/alerts/test',{},done)

    def add_airport(self):
        dialog=AirportDialog(parent=self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.model.save({'airports':[a for a in self.model.value['airports'] if a['code']!=dialog.airport['code']]+[dialog.airport]})

    def edit_airport(self):
        index=self.airport_table.currentRow()
        if 0<=index<len(self.model.value['airports']):
            dialog=AirportDialog(self.model.value['airports'][index],self)
            if dialog.exec()==QDialog.DialogCode.Accepted:
                airports=copy.deepcopy(self.model.value['airports']);airports[index]=dialog.airport;self.model.save({'airports':airports})

    def remove_airport(self):
        index=self.airport_table.currentRow()
        if 0<=index<len(self.model.value['airports']):
            airports=copy.deepcopy(self.model.value['airports']);airports.pop(index);self.model.save({'airports':airports})
