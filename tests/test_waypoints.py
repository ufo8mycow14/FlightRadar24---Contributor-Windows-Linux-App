import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from frw.waypoints import (BUNDLED, WaypointCatalog, WaypointStore, import_points,
                           read_bounded, validate_points)


class WaypointTests(unittest.TestCase):
    def test_bundled_reference_retains_source_cycle_licence_and_adelaide_fixes(self):
        doc=json.loads(BUNDLED.read_text('utf-8'))
        catalog=WaypointCatalog(doc)
        self.assertEqual(doc['licence'],'GPL-2.0-or-later')
        self.assertTrue(catalog.historical)
        self.assertEqual(catalog.cycle,'2013.10')
        self.assertEqual(len(catalog.points),5185)
        self.assertGreater(len(list(catalog.visible(137,-36,140,-34))),30)

    def test_csv_and_fix_formats_deduplicate_only_identical_points(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'points.csv'
            path.write_text('ident,latitude,longitude\nTESTA,-35,139\nTESTA,-35,139\nTESTA,-36,140\n',encoding='utf-8')
            points=import_points(path)
            self.assertEqual(len(points),2)
            for version, extra in [('600',''),('1101',' ENRT YB 0'),('1200',' ENRT YB 0')]:
                path=Path(temporary)/'fix.dat.gz'
                with gzip.open(path,'wt',encoding='utf-8') as stream:
                    stream.write(f'I\n{version} Version - fixture\n-35 139 TESTA{extra}\n-36 140 TESTA{extra}\n99\n')
                self.assertEqual(import_points(path),points)

    def test_invalid_or_oversized_import_does_not_replace_good_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=WaypointStore(temporary)
            path=Path(temporary)/'points.csv'
            path.write_text('ident,latitude,longitude\nTESTA,-35,139\n',encoding='utf-8')
            store.import_file(path,'fixture cycle')
            original=store.path.read_bytes()
            for row in ('BAD,nan,139','BAD,-35,181','BAD,-91,139','<script>,-35,139','BAD,,139'):
                path.write_text('ident,latitude,longitude\n'+row+'\n',encoding='utf-8')
                with self.assertRaises(ValueError):store.import_file(path)
                self.assertEqual(store.path.read_bytes(),original)
            with patch('frw.waypoints.MAX_BYTES',5):
                with self.assertRaises(ValueError):read_bounded(path)
            with self.assertRaises(ValueError):validate_points([('TEST',True,139)])
            for ident in (None, 123, True):
                with self.assertRaises(ValueError):validate_points([(ident,-35,139)])

    def test_import_selection_survives_restart_and_reference_keeps_previous_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'points.csv'
            path.write_text('ident,latitude,longitude\nTESTA,-35,139\n',encoding='utf-8')
            store=WaypointStore(temporary)
            store.import_file(path,'User date')
            self.assertEqual(WaypointStore(temporary).catalog().cycle,'User date')
            store.use_bundled()
            self.assertTrue(WaypointStore(temporary).catalog().historical)
            self.assertEqual(store.use_imported().points[0].ident,'TESTA')

    def test_visible_bounds_wrap_dateline_and_corruption_has_explicit_fallback(self):
        catalog=WaypointCatalog({'points':[['EAST',0,179],['WEST',0,-179],['FAR',0,0]]})
        self.assertEqual({p.ident for p in catalog.visible(178,-2,182,2)},{'EAST','WEST'})
        self.assertEqual(list(catalog.visible(1,1,2,2)),[])
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary)/'waypoints.json').write_text('{bad',encoding='utf-8')
            store=WaypointStore(temporary)
            self.assertIn('could not be read',store.error)
            self.assertTrue(store.catalog().historical)
