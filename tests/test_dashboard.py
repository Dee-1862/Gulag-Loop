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
        agent=self.snapshot([signal],books=books)['agents'][0]
        self.assertEqual(agent['state'],'fallback')
        self.assertEqual(agent['generation'],3)

    def test_saved_proposal_overrides_earlier_same_generation_progress(self):
        signal=dict(component='luminal',persona='Bold',state='retrying',generation=2,
                    updated_at=self.now-timedelta(seconds=10))
        book=dict(_id='p',persona='Bold',generation=2,fallback=True,
                  fallback_source='local_mutation',created_at=self.now)
        agent=self.snapshot([signal],books=[book])['agents'][0]
        self.assertEqual(agent['state'],'fallback')
        self.assertEqual(agent['updated_at'],self.now.isoformat())
        self.assertEqual(agent['fallback_source'],'local_mutation')

    def test_new_call_overrides_older_saved_generation(self):
        signal=dict(component='luminal',persona='Bold',state='complete',generation=1,updated_at=self.now)
        book=dict(_id='p',persona='Bold',generation=1,fallback=False)
        call=dict(persona='Bold',generation=2,ok=False,attempt=2,created_at=self.now)
        agent=self.snapshot([signal],calls=[call],books=[book])['agents'][0]
        self.assertEqual((agent['state'],agent['generation'],agent['attempt']),('error',2,2))

    def test_worker_order_prefers_recent_telemetry(self):
        workers=self.snapshot([
            dict(component='vision',worker='old',state='running',updated_at=self.now-timedelta(seconds=5)),
            dict(component='vision',worker='recent',state='running',updated_at=self.now),
        ])['workers']
        self.assertEqual([w['worker'] for w in workers],['recent','old'])

    def test_frame_matches_selected_worker_and_job_and_requires_freshness(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from dashboard.data import MongoSource
        source=MongoSource.__new__(MongoSource)
        collection=Mock()
        source.db=SimpleNamespace(dashboard_status=collection)
        collection.find_one.return_value=dict(frame='aW1hZ2U=',updated_at=self.now)
        self.assertIsNone(source.frame('test'))
        collection.find_one.assert_not_called()
        self.assertEqual(source.frame('test','worker-a','job-a'),b'image')
        query=collection.find_one.call_args.args[0]
        self.assertEqual(query['experiment_id'],'test')
        self.assertEqual(query['identity'],'worker-a')
        self.assertEqual(query['job_id'],'job-a')
        self.assertEqual(query['state'],'running')
        collection.find_one.return_value['updated_at']=self.now-timedelta(seconds=13)
        self.assertIsNone(source.frame('test','worker-a','job-a'))

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
        demo=DemoSource().snapshot('demo')
        self.assertEqual(demo['mode'],'demo')
        personas={a['persona']: a for a in demo['agents']}
        self.assertNotIn('aim_gain',personas['Defender']['changed_params'])
        self.assertEqual(set(personas['Explorer']['changed_params']),{'target_rule','strafe_mode'})
        with patch('dashboard.server.configuration',return_value={}):
            backend=Backend()
            data=backend.snapshot('x',False)
            self.assertFalse(data['connected'])
            self.assertEqual(data['mode'],'live')
            self.assertEqual(backend.snapshot('x',True)['mode'],'demo')

    def test_dotenv_quotes_comments_and_environment_override(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from dashboard.server import configuration
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'.env').write_text('MONGODB_URI="mongodb://example.invalid" # comment\nexport MONGODB_DB="file-db"\n')
            with patch('dashboard.server.ROOT',root), patch.dict(os.environ,{'MONGODB_DB':'env-db'},clear=True):
                config=configuration()
            self.assertEqual(config['MONGODB_URI'],'mongodb://example.invalid')
            self.assertEqual(config['MONGODB_DB'],'env-db')

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
