from pathlib import Path
import json, sys, unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'work/applibs')]
from frw.aircraft import Aircraft
from frw.config import validate
from frw.streams import Frame
from pyModeS import Message

EPOCH=1700000000
ICAO='ABCDEF'

def bits(*fields):
    """Build a 56-bit ME/MB from zero-based, most-significant-bit fields."""
    value=0
    for start,width,field in fields:
        assert 0<=field<1<<width
        value|=field<<(56-start-width)
    return value

def wire(payload,df=17,altitude_code=0):
    header=((df<<27)|(5<<24)|int(ICAO,16)) if df in (17,18) else ((df<<27)|altitude_code)
    value=(header<<80)|(payload<<24)
    remainder=value
    for index in range(88):
        if remainder&(1<<(111-index)):remainder^=0x1FFF409<<(87-index)
    parity=(remainder&0xFFFFFF)^(int(ICAO,16) if df in (20,21) else 0)
    return Frame((value|parity).to_bytes(14,'big'))

def altitude_code(altitude):
    n=(altitude+1000)//25
    return ((n&0x7E0)<<2)|((n&0x10)<<1)|(n&0xF)|0x10

def position(altitude,gnss=False):
    ac=altitude if gnss else altitude_code(altitude)
    if not gnss:ac=((ac>>7)<<6)|(ac&0x3F)
    return wire(bits((0,5,20 if gnss else 11),(8,12,ac)))

def velocity(subtype=1,speed=100,baro=True,rate=640,tas=False):
    fields=[(0,5,19),(5,3,subtype),(10,3,2),(35,1,int(baro)),
            (36,1,int(rate<0)),(37,9,abs(rate)//64+1),(49,7,5)]
    if subtype in (1,2):fields += [(14,10,speed+1),(25,10,1)]
    else:fields += [(13,1,1),(14,10,256),(24,1,int(tas)),(25,10,speed+1)]
    return wire(bits(*fields))

def identity():
    # Actual KLM1023 identification payload, with a test address and fresh CRC.
    return wire(int('202CC371C32CE0',16))

class AircraftDetailTests(unittest.TestCase):
    def setUp(self):
        self.aircraft=Aircraft(validate({'aircraft_ttl':3600}))

    def row(self,now=EPOCH):
        with patch('frw.aircraft.time.time',return_value=now):
            return self.aircraft.detail(ICAO)

    def test_only_identity_messages_advance_callsign_reception(self):
        observations = []
        self.aircraft.observer = lambda row, stamp: observations.append(row)
        for offset, frame in enumerate((identity(), position(10000), identity())):
            self.aircraft.frame(frame, EPOCH+offset)
        self.assertEqual([row.get('callsign_received') for row in observations], [EPOCH,None,EPOCH+2])
        self.assertEqual(observations[0]['callsign'], observations[1]['callsign'])

    def test_altitude_sources_remain_separate_and_alias_prefers_baro(self):
        self.aircraft.frame(position(35000),EPOCH)
        self.aircraft.frame(position(3000,gnss=True),EPOCH+10)
        row=self.row(EPOCH+10)
        self.assertEqual(row['altitude_baro'],35000)
        self.assertEqual(row['altitude_gnss'],9842)
        self.assertEqual(row['altitude'],35000)
        self.assertEqual(row['altitude_source'],'BARO')
        self.assertEqual(row['field_times']['altitude'],EPOCH)
        self.assertIn('TC20',row['field_sources']['altitude_gnss'])
        self.assertFalse(row['on_ground'])

    def test_air_ground_speed_heading_and_vertical_sources_are_not_interchanged(self):
        self.aircraft.frame(velocity(),EPOCH)
        self.aircraft.frame(velocity(3,250,False,-128),EPOCH+2)
        self.aircraft.frame(velocity(3,280,True,0,True),EPOCH+4)
        row=self.row(EPOCH+4)
        self.assertEqual(row['groundspeed'],100)
        self.assertEqual(row['track'],90)
        self.assertEqual(row['heading'],90)
        self.assertEqual(row['indicated_airspeed'],250)
        self.assertEqual(row['true_airspeed'],280)
        self.assertEqual(row['baro_vertical_rate'],0)
        self.assertEqual(row['gnss_vertical_rate'],-128)
        self.assertEqual(row['vertical_rate'],0)
        self.assertEqual(row['vr_source'],'BARO')
        self.assertEqual(row['geo_minus_baro'],100)
        self.assertNotIn('altitude_gnss',row)
        self.assertEqual(row['field_times']['groundspeed'],EPOCH)

    def test_category_emergency_and_quality_have_their_own_timestamps(self):
        self.aircraft.frame(wire(bits((0,5,4),(5,3,1))),EPOCH)
        self.aircraft.frame(wire(bits((0,5,28),(5,3,1),(8,3,2))),EPOCH+2)
        self.aircraft.frame(wire(bits((0,5,31),(40,3,2),(43,1,1),(44,4,9),
                                      (50,2,3),(52,1,1),(53,1,1),(54,1,1))),EPOCH+4)
        row=self.row(EPOCH+4)
        self.assertEqual((row['category'],row['category_typecode'],row['wake_vortex']),(1,4,'Light'))
        self.assertEqual(row['typecode'],31)
        self.assertEqual(row['emergency_state'],2)
        self.assertEqual(row['squawk'],'0000')
        self.assertEqual((row['version'],row['nac_p'],row['sil']),(2,9,3))
        self.assertEqual((row['nic_baro'],row['nic_supplement_a'],row['sil_supplement'],row['hrd']),(1,1,1,1))
        self.assertEqual(row['field_times']['category'],EPOCH)
        self.assertEqual(row['field_ages']['emergency_state'],2)

    def test_target_state_status_bits_and_reserved_subtype(self):
        target=bits((0,5,29),(5,2,1),(8,1,1),(9,11,1001),(20,9,267),
                    (29,1,1),(30,9,128),(46,1,1),(47,1,1),(48,1,1),(52,1,1))
        self.aircraft.frame(wire(target),EPOCH)
        row=self.row()
        self.assertEqual(row['selected_altitude'],32000)
        self.assertEqual(row['selected_altitude_source'],'FMS')
        self.assertAlmostEqual(row['baro_pressure_setting'],1012.8)
        self.assertEqual(row['selected_heading'],90)
        self.assertTrue(row['autopilot']);self.assertFalse(row['altitude_hold_mode'])
        self.aircraft.frame(wire(target&~(3<<49)),EPOCH+2)
        self.assertEqual(self.row(EPOCH+2)['field_times']['autopilot'],EPOCH)
        self.aircraft.frame(wire(target&~(1<<9)),EPOCH+4)
        self.assertNotIn('autopilot',self.row(EPOCH+4))
        self.assertEqual(self.row(EPOCH+4)['field_times']['autopilot'],EPOCH)

    def test_reserved_velocity_and_operational_formats_do_not_create_values(self):
        self.aircraft.frame(velocity(subtype=0),EPOCH)
        self.aircraft.frame(wire(bits((0,5,31),(5,3,3),(40,3,2),(44,4,9))),EPOCH+2)
        row=self.row(EPOCH+2)
        for field in ('vertical_rate','geo_minus_baro','nac_v','nac_p','version'):
            self.assertNotIn(field,row)

    def test_ambiguous_commb_payload_never_overwrites_observations(self):
        self.aircraft.frame(identity(),EPOCH)
        ambiguous=wire(1<<55,df=20,altitude_code=altitude_code(35000))
        decoded=Message(ambiguous.data).decode()
        self.assertGreater(len(decoded['bds_candidates']),1)
        self.aircraft.frame(ambiguous,EPOCH+2)
        row=self.row(EPOCH+2)
        self.assertEqual(row['altitude_baro'],35000)
        self.assertEqual(row['callsign'],'KLM1023')
        self.assertGreater(len(row['bds_candidates']),1)
        self.assertNotIn('bds',row)
        for field in ('roll','magnetic_heading','selected_altitude_mcp','supported_bds'):
            self.assertNotIn(field,row)
        self.assertIn('header',row['field_sources']['altitude_baro'])

    def test_unambiguous_commb_keeps_target_state_air_and_ground_quantities(self):
        self.aircraft.frame(identity(),EPOCH)
        heading=bits((0,1,1),(2,10,512),(12,1,1),(13,10,250),(23,1,1),
                     (24,10,175),(34,1,1),(36,9,16),(45,1,1),(47,9,15))
        target=bits((0,1,1),(1,12,2000),(13,1,1),(14,12,2100),(26,1,1),
                    (27,12,2130),(47,1,1),(48,1,1),(49,1,1),(53,1,1),(54,2,3))
        track=bits((0,1,1),(2,9,32),(11,1,1),(13,10,512),(23,1,1),
                   (24,10,200),(34,1,1),(36,9,32),(45,1,1),(46,10,210))
        for i,(payload,register) in enumerate(((heading,'6,0'),(target,'4,0'),(track,'5,0'))):
            report=wire(payload,20,altitude_code(35000))
            decoded=Message(report.data).decode()
            self.assertEqual(decoded['bds'],register)
            self.assertNotIn('bds_candidates',decoded)
            self.aircraft.frame(report,EPOCH+2+i*2)
        row=self.row(EPOCH+6)
        self.assertEqual(row['magnetic_heading'],90)
        self.assertEqual(row['indicated_airspeed'],250)
        self.assertAlmostEqual(row['mach'],.7)
        self.assertEqual((row['baro_vertical_rate'],row['inertial_vertical_rate']),(512,480))
        self.assertEqual((row['groundspeed'],row['true_airspeed'],row['true_track']),(400,420,90))
        self.assertEqual((row['roll'],row['track_rate']),(5.625,1))
        self.assertEqual((row['selected_altitude_mcp'],row['selected_altitude_fms']),(32000,33600))
        self.assertEqual(row['baro_pressure_setting'],1013)
        self.assertEqual(row['target_altitude_source'],'fms')
        self.assertEqual(row['field_times']['indicated_airspeed'],EPOCH+2)
        self.assertIn('BDS 6,0',row['field_sources']['indicated_airspeed'])
        self.assertIn('BDS 5,0',row['field_sources']['true_track'])
        self.assertNotIn('heading',row)

    def test_expiry_preserves_field_times_and_falls_back_to_fresh_altitude(self):
        self.aircraft.frame(velocity(),EPOCH)
        self.aircraft.frame(position(35000),EPOCH)
        self.aircraft.frame(position(3000,True),EPOCH+10)
        self.aircraft.frame(identity(),EPOCH+60)
        row=self.row(EPOCH+61)
        self.assertNotIn('altitude_baro',row)
        self.assertNotIn('groundspeed',row)
        self.assertEqual(row['field_times']['groundspeed'],EPOCH)
        self.assertEqual(row['field_ages']['groundspeed'],61)
        self.assertEqual(row['altitude'],9842)
        self.assertEqual(row['altitude_source'],'GNSS')
        self.assertEqual(row['field_times']['altitude'],EPOCH+10)
        self.assertEqual(row['seen'],1)
        self.assertIn('callsign',row)

    def test_sparse_history_is_bounded_and_identity_does_not_add_samples(self):
        for i in range(400):
            self.aircraft.frame(velocity(speed=100+i//4),EPOCH+i*2)
            if i%2==0:self.aircraft.frame(position(30000+i*25),EPOCH+i*2+.5)
        before=self.row(EPOCH+799)['history']
        self.aircraft.frame(identity(),EPOCH+800)
        row=self.row(EPOCH+800)
        self.assertEqual(len(row['history']),300)
        self.assertEqual(row['history'],before)
        self.assertGreaterEqual(row['history'][0]['time'],EPOCH+200)
        self.assertEqual(row['history'],sorted(row['history'],key=lambda p:p['time']))
        self.assertNotIn('altitude_baro',row['history'][-1])
        point=row['history'][-2]
        self.assertEqual(point['field_times']['altitude_baro'],point['time']+.5)
        json.dumps(row,allow_nan=False)
        with patch('frw.aircraft.time.time',return_value=EPOCH+800):
            self.assertNotIn('history',self.aircraft.snapshot()['aircraft'][0])
        self.aircraft.frame(identity(),EPOCH+1400)
        self.assertEqual(self.row(EPOCH+1400)['history'],[])

    def test_responses_do_not_share_mutable_state(self):
        self.aircraft.frame(position(35000),EPOCH)
        row=self.row()
        row['field_times']['altitude_baro']=0
        row['field_sources']['altitude_baro']='changed'
        row['history'][0]['field_times']['altitude_baro']=0
        row['history'][0]['altitude_baro']=0
        row['trail'].append([1,2,3])
        fresh=self.row()
        self.assertEqual(fresh['field_times']['altitude_baro'],EPOCH)
        self.assertEqual(fresh['history'][0]['altitude_baro'],35000)
        self.assertEqual(fresh['history'][0]['field_times']['altitude_baro'],EPOCH)
        self.assertNotEqual(fresh['field_sources']['altitude_baro'],'changed')
        self.assertNotIn([1,2,3],fresh['trail'])

    def test_sbs_keeps_unspecified_sources_and_legacy_values(self):
        fields=['MSG','4','1','1',ICAO,'1','','','','','TEST123','12000','210','45','','','512','1200','','','','0']
        self.aircraft.ingest_sbs(','.join(fields),EPOCH)
        row=self.row()
        self.assertEqual(row['data_source'],'SBS/BaseStation input')
        self.assertEqual(row['altitude'],12000)
        self.assertEqual(row['vertical_rate'],512)
        self.assertEqual(row['altitude_source'],'SBS unspecified')
        self.assertEqual(row['vr_source'],'SBS unspecified')
        self.assertNotIn('altitude_baro',row)
        self.assertNotIn('baro_vertical_rate',row)
        self.assertEqual(row['history'][0]['altitude'],12000)
        output=Aircraft.sbs(row,4,EPOCH).decode().strip().split(',')
        self.assertEqual((float(output[12]),float(output[13]),float(output[16])),(210,45,512))
        fields[12]='nan';self.aircraft.ingest_sbs(','.join(fields),EPOCH+1)
        self.assertEqual(self.aircraft.total,1)

    def test_untrusted_commb_and_invalid_crc_are_rejected(self):
        self.aircraft.frame(wire(1<<55,20,altitude_code(35000)),EPOCH)
        good=identity();bad=Frame(good.data[:-1]+bytes([good.data[-1]^1]))
        self.aircraft.frame(bad,EPOCH)
        self.assertIsNone(self.row())
        self.assertEqual(self.aircraft.rejected,2)

    def test_unavailable_report_blocks_delayed_observation(self):
        target=bits((0,5,29),(5,2,1),(46,1,1),(47,1,1))
        self.aircraft.frame(wire(target),EPOCH)
        self.aircraft.frame(wire(target&~(1<<9)),EPOCH+10)
        self.aircraft.frame(wire(target),EPOCH+5)
        row=self.row(EPOCH+10)
        self.assertNotIn('autopilot',row)
        self.assertEqual(row['field_times']['autopilot'],EPOCH)

    def test_sbs_does_not_retimestamp_cached_measurements(self):
        self.aircraft.frame(velocity(),EPOCH)
        invalid=self.aircraft.frame(velocity(subtype=0),EPOCH+1).decode().strip().split(',')
        self.assertEqual(invalid[1],'8')
        self.assertEqual((invalid[12],invalid[13],invalid[16]),('','',''))
        # A surface movement report has no resolved position without a local
        # reference or CPR pair. Previously it could repeat cached coordinates.
        with self.aircraft.lock:
            self.aircraft._update(ICAO,{'df':17,'typecode':5,'latitude':1,'longitude':2},EPOCH+2)
        unresolved=self.aircraft.frame(wire(bits((0,5,5))),EPOCH+3).decode().strip().split(',')
        self.assertEqual((unresolved[14],unresolved[15]),('',''))

    def test_out_of_range_position_returns_sbs_bytes_without_coordinate(self):
        self.aircraft=Aircraft(validate({'latitude':-34.9,'longitude':138.6,'range_nm':50}))
        self.aircraft.frame(Frame(bytes.fromhex('8D40621D58C382D690C8AC2863A7')),EPOCH)
        result=self.aircraft.frame(Frame(bytes.fromhex('8D40621D58C386435CC412692AD6')),EPOCH+1)
        self.assertIsInstance(result,bytes)
        with patch('frw.aircraft.time.time',return_value=EPOCH+1):
            self.assertNotIn('latitude',self.aircraft.detail('40621D'))

if __name__=='__main__':unittest.main(verbosity=2)
