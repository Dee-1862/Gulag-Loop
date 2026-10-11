"""Clearly labeled synthetic data for UI previews; never touches MongoDB or Luminal."""
from datetime import datetime, timedelta, timezone
import time
from dashboard.data import assemble

class DemoSource:
    def experiments(self):
        return [dict(id='demo-kino', map='kino', generation=8)]

    def snapshot(self, experiment_id):
        now = datetime.now(timezone.utc)
        phase = int(time.time()) % 36
        names = ['Bold','Tuner','Defender','Explorer']
        rationales = [
            'Reduce aim gain to address overshoot. Retreat earlier when zombies fill the frame.',
            'Lower aim gain by 10%. Keep the remaining settings stable to isolate the effect on target lock.',
            'Start retreating sooner and alternate strafing to create space before reloading.',
            'Prioritize the nearest threat and test alternating movement while keeping aim settings fixed.',
        ]
        changes = [['aim_gain','retreat_box_h'], ['aim_gain'], ['retreat_box_h','strafe_mode'], ['target_rule','strafe_mode']]
        books = [dict(_id=f'p{i}', persona=name, generation=8, rationale=rationales[i], changed_params=changes[i], fallback=False) for i,name in enumerate(names)]
        baseline = dict(det_conf=.35, aim_gain=.8, aim_max_step=80, aim_y_offset=.15,
                        fire_radius_px=25, burst_ms=120, retreat_box_h=300, strafe_mode='none',
                        strafe_period_s=2., reload_when_clear_at=3, scan_turn_px=60, target_rule='center')
        adjustments = [dict(aim_gain=.72, retreat_box_h=280), dict(aim_gain=.72),
                       dict(retreat_box_h=280, strafe_mode='alternate'), dict(target_rule='nearest', strafe_mode='alternate')]
        for book, adjustment in zip(books, adjustments):
            book.update(params={**baseline, **adjustment}, parent_ids=['demo-baseline'],
                        model='deepseek-ai/DeepSeek-V4.1-Flash', created_at=now-timedelta(minutes=5))
        books.append(dict(_id='demo-baseline', persona='Baseline', generation=0, params=baseline,
                          parent_ids=[], rationale='Hand-written starting strategy.', created_at=now-timedelta(hours=1)))
        runs = [dict(_id=f'r{g}', job_id=f'j{g}', strategy_id=f'p{g%4}', generation=g, worker='game-laptop-01', points=180+g*95,
                     survival_s=90+g*10, fitness=630+g*145, diagnostics={'overshoot_rate':.18,'hit_rate':.72,'time_no_target_pct':14.2,'det_count_avg':2.3,'loop_fps':29.6,'shots_fired':145,'hits_proxy':104,'last_5s_summary':'Demo example: several targets approached during a reload. Earlier retreat may create more room.'}, completed_at=now-timedelta(seconds=(8-g)*40+12)) for g in range(8,-1,-1)]
        jobs = [dict(job_id=f'job{i}', kind='child', status='done' if i<3 else 'queued', created_at=now-timedelta(seconds=50+i)) for i in range(5)]
        signals = [dict(component='vision', identity='game-laptop-01', worker='game-laptop-01', state='running' if phase<18 else 'complete', updated_at=now,
                        fps=29.6, detections=3, action='RETREAT' if phase%7<2 else 'ENGAGE', points=940, elapsed_s=102+phase)]
        if phase < 18:
            jobs.append(dict(job_id='active',kind='child',status='running',worker='game-laptop-01',claimed_at=now-timedelta(seconds=2)))
        calls=[]
        for i,name in enumerate(names):
            state='running' if 20<=phase<28+i else 'complete'
            signals.append(dict(component='luminal',persona=name,state=state,generation=8,attempt=1,updated_at=now))
            calls.append(dict(persona=name,model='deepseek-ai/DeepSeek-V4.1-Flash',generation=8,strategy_id=f'p{i}',attempt=1,ok=True,latency_ms=3200+i*780,tokens={'total_tokens':1400+i*130},created_at=now-timedelta(seconds=15+i)))
        result=assemble(dict(_id='demo-kino',map='kino',generation=8),jobs,runs,calls,books,signals,dict(jobs=42,runs=37,playbooks=33,llm_calls=35),now)
        result['mode']='demo'
        return result
