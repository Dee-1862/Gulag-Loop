from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
import json
from dashboard.data import assemble, age
from dashboard.demo import DemoSource
from dashboard.server import Backend
from common.monitoring import Reporter

class DashboardTests(TestCase):
    def test_recordings_include_all_history_and_project_only_public_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from dashboard.data import MongoSource
        source = MongoSource.__new__(MongoSource)
        footage = Mock()
        source.db = SimpleNamespace(footage=footage)
        rows = [dict(experiment_id='archived', job_id=f'job-{i}', created_at=self.now,
                     file_id='internal-file-id', private_note='not public') for i in range(50)]
        footage.find.return_value.sort.return_value = rows
        result = source.recordings()
        self.assertEqual(len(result), 50)
        self.assertEqual(result[-1]['job_id'], 'job-49')
        self.assertEqual(footage.find.call_args.args[0], {'kind': 'hud_video'})
        self.assertNotIn('private_note', result[0])
        self.assertNotIn('file_id', result[0])
        self.assertEqual(result[0]['created_at'], self.now.isoformat())
        source.recordings('one-experiment')
        self.assertEqual(footage.find.call_args.args[0], {'kind': 'hud_video', 'experiment_id': 'one-experiment'})

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

    def test_saved_records_include_settings_lineage_and_zero_scores(self):
        book=dict(_id='baseline',persona='Baseline',generation=0,parent_ids=[],
                  params={'aim_gain':.8,'target_rule':'center','unrelated_secret':'do-not-return'},
                  rationale='Starting settings.', unrelated_secret='do-not-return')
        run=dict(job_id='job',strategy_id='baseline',fitness=0,points=0,survival_s=0,
                 diagnostics={'loop_fps':16.1,'frame':'private-image'})
        data=self.snapshot(runs=[run],books=[book])
        saved=data['records']['strategies'][0]
        self.assertEqual(saved['params'],{'aim_gain':.8,'target_rule':'center'})
        self.assertEqual(saved['parent_ids'],[])
        self.assertEqual((saved['evaluations'],saved['mean_fitness']),(1,0))
        self.assertEqual(data['records']['runs'][0]['diagnostics'],{'loop_fps':16.1})
        self.assertNotIn('do-not-return',json.dumps(data))

    def test_record_lists_are_bounded_and_do_not_include_raw_provider_content(self):
        data=self.snapshot(runs=[dict(job_id=str(i),strategy_id='p',fitness=i) for i in range(60)],
                           books=[dict(_id=str(i),generation=i) for i in range(60)],
                           calls=[dict(persona='Bold',generation=1,ok=False,error_type='ValueError',
                                       raw_response='do-not-return') for _ in range(30)])
        self.assertEqual(len(data['records']['runs']),25)
        self.assertEqual(len(data['records']['strategies']),50)
        self.assertEqual(len(data['records']['calls']),20)
        self.assertEqual(data['records']['calls'][0]['error_type'],'ValueError')
        self.assertNotIn('do-not-return',json.dumps(data))

    def test_proposal_comparison_keeps_parent_outside_visible_records(self):
        child = dict(_id='child', persona='Bold', generation=2, parent_ids=['parent'],
                     params={'aim_gain': .72}, changed_params=['aim_gain'], rationale='Reduce overshoot')
        books = [child] + [dict(_id=f'other-{i}') for i in range(55)] + [
            dict(_id='parent', params={'aim_gain': .8, 'api_key': 'do-not-return'})]
        data = self.snapshot(books=books)
        self.assertEqual(data['agents'][0]['proposal']['parent_params'], {'aim_gain': .8})
        self.assertEqual(data['records']['strategies'][0]['parent_params'], {'aim_gain': .8})
        self.assertNotIn('do-not-return', json.dumps(data))
        child['parent_ids'] = ['missing', 'parent']
        self.assertIsNone(self.snapshot(books=books)['agents'][0]['proposal']['parent_params'])

    def test_console_tail_is_bounded_and_redacts_credentials(self):
        from dashboard.control import console_tail
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'worker.log'
            path.write_text('old line\n' * 10000 + '[12:01] stored in MongoDB\n'
                            'mongodb+srv://user:secret@host/db api_key=secret Bearer abc123 sk-example\n')
            lines = console_tail(path)
            self.assertLessEqual(len(lines), 48)
            self.assertIn('[12:01] stored in MongoDB', lines)
            self.assertNotIn('secret', '\n'.join(lines))
            self.assertNotIn('abc123', '\n'.join(lines))
            self.assertNotIn('sk-example', '\n'.join(lines))
            self.assertEqual(console_tail(Path(folder) / 'missing'), [])

    def test_demo_is_explicit_and_no_credentials_means_disconnected(self):
        demo=DemoSource().snapshot('demo')
        self.assertEqual(demo['mode'],'demo')
        personas={a['persona']: a for a in demo['agents']}
        self.assertNotIn('aim_gain',personas['Defender']['changed_params'])
        self.assertEqual(set(personas['Explorer']['changed_params']),{'target_rule','strafe_mode'})
        with patch('dashboard.server.configuration',return_value={}):
            backend=Backend()
            data=backend.snapshot('x')
            self.assertFalse(data['connected'])
            self.assertEqual(data['mode'],'live')

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

    def test_empty_experiment_loads_unified_history(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with patch('dashboard.server.configuration', return_value={}):
            backend=Backend()
        backend.live=SimpleNamespace(snapshot=Mock(return_value={'scope':'all'}))
        self.assertEqual(backend.snapshot(''), {'scope':'all'})
        backend.live.snapshot.assert_called_once_with('')

    def test_combined_history_keeps_parent_scores_and_agent_recency_in_their_experiments(self):
        from dashboard.data import assemble_history
        experiments=[{'_id':'old'},{'_id':'new'}]
        before=self.now-timedelta(days=1)
        books=[dict(_id='parent',experiment_id='old',params={'aim_gain':.9},created_at=before),
               dict(_id='child',experiment_id='old',persona='Bold',generation=99,parent_ids=['parent'],params={'aim_gain':.8},created_at=before),
               dict(_id='parent',experiment_id='new',params={'aim_gain':.7},created_at=self.now),
               dict(_id='child',experiment_id='new',persona='Bold',generation=1,parent_ids=['parent'],params={'aim_gain':.6},created_at=self.now)]
        runs=[dict(_id='new-run',experiment_id='new',strategy_id='child',fitness=20,completed_at=self.now),
              dict(_id='old-run',experiment_id='old',strategy_id='child',fitness=999,completed_at=before)]
        data=assemble_history(experiments,[],runs,[],books,[],{'runs':2},self.now)
        self.assertEqual(data['experiment_count'],2)
        self.assertEqual([r['_id'] for r in data['records']['runs']],['new-run','old-run'])
        agent=data['agents'][0]
        self.assertEqual(agent['experiment_id'],'new')
        self.assertEqual(agent['proposal']['parent_params'],{'aim_gain':.7})
        self.assertEqual(agent['proposal']['mean_fitness'],20)
        self.assertEqual(agent['proposal']['evaluations'],1)
        self.assertEqual(data['events'][0]['experiment_id'],'new')

    def test_removed_demo_api_cannot_return_synthetic_records(self):
        from dashboard.server import handler_for
        from unittest.mock import Mock
        backend=Mock()
        handler=handler_for(backend).__new__(handler_for(backend))
        handler.path='/api/snapshot?mode=demo'
        handler.send=Mock()
        handler.do_GET()
        self.assertEqual(handler.send.call_args.kwargs['status'],400)
        backend.snapshot.assert_not_called()
