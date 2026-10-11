from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
import json
from dashboard.data import assemble, age
from dashboard.demo import DemoSource
from dashboard.server import Backend
from common.monitoring import Reporter

class DashboardTests(TestCase):
    def snapshot(self, signals=(), runs=(), calls=(), books=()):
        return assemble({'_id':'test','generation':1,'map':'kino'}, [], list(runs), list(calls), list(books), list(signals), {}, self.now)

    def setUp(self):
        self.now=datetime.now(timezone.utc)

    def test_fresh_worker_and_expired_worker(self):
        signal=dict(component='vision',state='running',worker='pc',updated_at=self.now)
        self.assertEqual(self.snapshot([signal])['workers'][0]['state'],'running')
        signal['updated_at']=self.now-timedelta(seconds=13)
        self.assertEqual(self.snapshot([signal])['workers'][0]['state'],'stale')
        signal['state']='complete'
        self.assertEqual(self.snapshot([signal])['workers'][0]['state'],'complete')

    def test_agent_progress_expiry_fallback_and_newer_saved_generation(self):
        signal=dict(component='luminal',persona='Bold',state='running',generation=2,updated_at=self.now)
        self.assertEqual(self.snapshot([signal])['agents'][0]['state'],'running')
        signal['updated_at']=self.now-timedelta(seconds=241)
        self.assertEqual(self.snapshot([signal])['agents'][0]['state'],'stale')
        books=[dict(_id='p',persona='Bold',generation=3,fallback=True)]
        self.assertEqual(self.snapshot([signal],books=books)['agents'][0]['state'],'fallback')

    def test_chart_and_mean_fitness_from_evaluations(self):
        runs=[dict(strategy_id='a',fitness=100,generation=1),dict(strategy_id='a',fitness=300,generation=1),dict(strategy_id='b',fitness=250,generation=2)]
        data=self.snapshot(runs=runs)
        self.assertEqual(data['leaderboard'][0]['strategy_id'],'b')
        self.assertEqual(data['chart'][0],dict(generation=1,best=300,mean=200))
        self.assertEqual(len(data['agents']),4)

    def test_frames_excluded_from_snapshot(self):
        data=self.snapshot([dict(component='vision',state='running',updated_at=self.now,frame='private-frame')])
        self.assertNotIn('private-frame',json.dumps(data))

    def test_demo_is_explicit_and_no_credentials_means_disconnected(self):
        self.assertEqual(DemoSource().snapshot('demo')['mode'],'demo')
        with patch('dashboard.server.configuration',return_value={}):
            backend=Backend()
            data=backend.snapshot('x',False)
            self.assertFalse(data['connected'])
            self.assertEqual(data['mode'],'live')
            self.assertEqual(backend.snapshot('x',True)['mode'],'demo')

    def test_progress_writer_routes_four_agents_separately(self):
        documents={}
        class Collection:
            def replace_one(self, query, doc, upsert):
                documents[query['_id']]=doc
        reporter=Reporter({'dashboard_status':Collection()},'test','luminal','agents')
        for persona in ['Bold','Tuner','Defender','Explorer']:
            reporter.emit(persona=persona,state='running')
        reporter.close()
        self.assertEqual(len(documents),4)
        self.assertEqual(documents['test:luminal:Bold']['experiment_id'],'test')

    def test_monitoring_failure_is_nonfatal(self):
        class Broken:
            def replace_one(self,*args,**kwargs):
                raise RuntimeError('Unavailable')
        reporter=Reporter({'dashboard_status':Broken()},'test','vision','pc')
        reporter.emit(state='running')
        reporter.close()
        self.assertFalse(reporter.thread.is_alive())

    def test_invalid_timestamp_is_not_fresh(self):
        self.assertIsNone(age('bad timestamp',self.now))

    def test_empty_experiment_checks_connection(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with patch('dashboard.server.configuration', return_value={}):
            backend=Backend()
        command=Mock(side_effect=RuntimeError('offline'))
        backend.live=SimpleNamespace(db=SimpleNamespace(command=command))
        with self.assertRaises(RuntimeError):
            backend.snapshot('',False)
        command.assert_called_once_with('ping')
