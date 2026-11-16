"""Known-only score/margin diagnostics. No official policy without unknown data.

Uses cached labelled validation vectors. Test is audited and left unconfirmed;
Test scores never determine candidate cutoffs or selection. No DeepFace rerun.
"""
from collections import Counter
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from convert_images import DATASET,OUTPUTS,sha256,protect_originals,verify_originals
from create_embeddings import atomic_json
from validate import preflight
from face_service import FaceMatcher,gate_match,pipeline_signature

OUTPUT_NAMES={'threshold_evaluation.csv','recognition_threshold.json','threshold_summary.json'}


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def evaluate_rule(known,min_similarity=None,min_margin=None):
    """Unknown FAR is not estimable from known-person outcomes."""
    accepted=[r for r in known if (min_similarity is None or r['best_similarity']>=min_similarity)
              and (min_margin is None or r['margin']>=min_margin)]
    correct=sum(r['best_candidate']==r['true_student'] for r in accepted)
    rejected=len(known)-len(accepted)
    return dict(known_samples=len(known),known_accepted=len(accepted),known_correct_accepted=correct,
                known_wrong_identity_accepted=len(accepted)-correct,known_rejected=rejected,
                known_correct_identification_rate=correct/len(known),
                known_false_rejection_rate=rejected/len(known),
                known_wrong_identification_rate=(len(accepted)-correct)/len(known),
                unknown_samples=0,unknown_false_accepts=None,unknown_false_accept_rate=None)


def cutoffs(values):
    # Observed score breakpoints and a just-above-maximum endpoint. No hand-picked values.
    values=sorted(set(float(x) for x in values))
    return values+[float(np.nextafter(values[-1],np.inf))]


def run():
    OUTPUTS.mkdir(exist_ok=True)
    protected={p:sha256(p) for p in OUTPUTS.iterdir() if p.is_file() and p.name not in OUTPUT_NAMES}
    before=protect_originals()
    try:
        config,settings,records,students,samples,versions=preflight()
        gallery_hash=sha256(OUTPUTS/'face_embeddings.json')
        signature=pipeline_signature(config,gallery_hash)
        matcher=FaceMatcher(records,config,gallery_hash)
        report=read_csv(OUTPUTS/'validation_report.csv')
        test_report=read_csv(OUTPUTS/'test_report.csv')
        cache=json.loads((OUTPUTS/'validation_embedding_cache.json').read_text(encoding='utf-8'))
        expected_cache_signature=hashlib.sha256(json.dumps(dict(settings=settings,versions=versions,
            gallery_sha256=gallery_hash,weights=config['weights'],
            preprocessing=config['input_preprocessing']),sort_keys=True).encode()).hexdigest()
        if cache.get('pipeline_signature')!=expected_cache_signature or cache.get('split')!='validation':
            raise ValueError('Validation cache does not match enrollment pipeline')
        labelled={r['source']:r for r in report}
        if len(labelled)!=len(report) or set(labelled)!={r['source'] for r in samples}:
            raise ValueError('Labelled validation report does not cover all validation inputs exactly')
        known=[]
        for sample in samples:
            entry=cache['records'][sample['source']]
            row=labelled[sample['source']]
            if (entry['sha256']!=sample['sha256'] or entry['student']!=sample['student']
                    or entry['face_count']!=1 or row['status']!='ok'
                    or row['true_student']!=sample['student']):
                raise ValueError(f'Invalid cached validation provenance: {sample["source"]}')
            match=matcher.match(entry['embedding'])
            if (match['best_candidate']!=row['predicted_student']
                    or match['second_candidate']!=row['second_best_student']
                    or abs(match['best_similarity']-float(row['cosine_similarity']))>1e-12
                    or abs(match['second_similarity']-float(row['second_best_similarity']))>1e-12):
                raise ValueError(f'Validation scores differ from saved report: {sample["source"]}')
            known.append(dict(**match,true_student=sample['student'],source=sample['source']))

        # Explicit labels only: never turn nearest candidates, high scores, or Test
        # faces into unknown/known ground truth. Test remains outside threshold fitting.
        test_labelled=[r for r in test_report if r.get('ground_truth','').strip()]
        test_unknown_labelled=[r for r in test_labelled if r['ground_truth'] not in students]
        other_folders=[p.name for p in DATASET.iterdir() if p.is_dir()
                       and p.name not in ('registered','validation','Test')]
        unknown_audit=dict(known_validation_samples=len(known),
            known_validation_identities=students,test_face_rows=len(test_report),
            test_faces_with_explicit_ground_truth=len(test_labelled),
            test_unknown_faces_with_explicit_ground_truth=len(test_unknown_labelled),
            additional_dataset_folders=other_folders,independent_labelled_unknown_calibration_samples=0,
            conclusion='No independent labelled unknown-person calibration/evaluation dataset is configured.')
        if other_folders:
            raise ValueError('Additional dataset folders need explicit label review before known-only diagnostics: '+str(other_folders))

        score_cuts=cutoffs(r['best_similarity'] for r in known)
        margin_cuts=cutoffs(r['margin'] for r in known)
        evaluations=[]
        def add(kind,score=None,margin=None):
            evaluations.append(dict(candidate_id=len(evaluations),rule=kind,min_similarity=score,min_margin=margin,
                scope='known_validation_only_diagnostic_not_official',official_candidate_selected=False,
                **evaluate_rule(known,score,margin)))
        add('no_rejection_baseline')
        for score in score_cuts:
            add('similarity_only',score)
        for margin in margin_cuts:
            add('margin_only',None,margin)
        for score in score_cuts:
            for margin in margin_cuts:
                add('similarity_and_margin',score,margin)

        policy=dict(schema_version=1,status='insufficient_labelled_unknown_data',enabled=False,
            official=False,min_similarity=None,min_margin=None,pipeline_signature=signature,
            model_name=settings['model_name'],detector_backend=settings['detector_backend'],
            align=settings['align'],normalization=settings['normalization'],
            score_definition='Maximum sample cosine per identity; top two must be different identities.',
            acceptance_rule='best_similarity >= min_similarity AND margin >= min_margin',
            action_when_uncalibrated='unconfirmed; do not assert an identity or record attendance',
            automatic_identity_confirmation=False,
            reason='No independent labelled unknown-person data; FAR and threshold selection cannot be validated.',
            evidence=dict(labelled_known_validation_samples=len(known),labelled_unknown_validation_samples=0,
                          independent_evaluation_completed=False,test_used_for_threshold_optimization=False),
            created_at_utc=datetime.now(timezone.utc).isoformat())
        applied_test=Counter()
        for row in test_report:
            if row['processing_status']!='ok':
                continue
            decision=gate_match(row['nearest_candidate'],float(row['cosine_similarity']),
                row['second_candidate'],float(row['second_similarity']),policy,signature)
            assert not decision['identity_confirmed'] and not decision['policy_applied']
            applied_test[decision['decision']]+=1

        baseline=evaluate_rule(known)
        hardest=sorted(known,key=lambda r:r['margin'])[:5]
        summary=dict(schema_version=1,status=policy['status'],official_threshold_created=False,
            selected_configuration=None,pipeline_signature=signature,settings=settings,
            labelled_data_audit=unknown_audit,baseline_known_validation=baseline,
            known_per_student=dict(Counter(r['true_student'] for r in known)),
            candidate_rows=len(evaluations),score_candidates=len(score_cuts),margin_candidates=len(margin_cuts),
            candidate_generation='Observed labelled known-validation scores/margins plus just-above-max endpoints; no Test scores.',
            joint_candidates_accepting_all_known_samples=sum(r['rule']=='similarity_and_margin'
                and r['known_rejected']==0 for r in evaluations),
            observed_known_only_all_pass_bounds=dict(max_similarity_cutoff_for_this_sample=min(r['best_similarity'] for r in known),
                max_margin_cutoff_for_this_sample=min(r['margin'] for r in known),
                warning='Descriptive limits on these 25 known samples only; NOT recommended or official thresholds.'),
            smallest_known_margins=hardest,unknown_false_accept_rate=None,
            official_known_false_rejection_rate=None,
            unknown_metric_note='Missing unknown examples is not evidence of zero false accepts.',
            test_used_for_threshold_optimization=False,test_used_for_independent_accuracy_claim=False,
            test_application_of_disabled_policy=dict(applied_test),
            comparison_conclusion='Known retention/rejection trade-offs measured; unknown FAR unavailable, so no configuration can be selected responsibly.',
            metric_definitions=dict(known_correct_identification_rate='accepted and correct / all known samples',
                unknown_false_accept_rate='unknown accepted as any registered identity / all labelled unknown samples',
                known_false_rejection_rate='known rejected by score/margin rule / all known samples'),
            data_needed=['Explicit human-verified unknown-person identities absent from the registered gallery.',
                'Image path and face_id/bounding box for group faces; person_id and enrollment_status labels.',
                'Separate calibration and held-out evaluation partitions for known and unknown people.',
                'Prefer unknown identities disjoint across partitions; include deployment-like lighting, pose and face sizes.',
                'An application-approved false-accept/false-reject trade-off before selecting a policy.',
                'Keep DATASET/Test held out; do not fit thresholds to its current scores.'],
            limitations=['25 known images are a small diagnostic set, not independent threshold-validation evidence.',
                         'Reject-all can look safe without proving recognition usefulness; both known and unknown metrics are needed.'],
            protected_input_sha256={p.name:digest for p,digest in protected.items()},
            dataset_unchanged=True,preexisting_outputs_unchanged=True,
            completed_at_utc=datetime.now(timezone.utc).isoformat())

        verify_originals(before)
        for path,digest in protected.items():
            if sha256(path)!=digest:
                raise RuntimeError(f'Protected artifact changed: {path}')
        with (OUTPUTS/'threshold_evaluation.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(evaluations[0]))
            writer.writeheader(); writer.writerows(evaluations)
        atomic_json(OUTPUTS/'recognition_threshold.json',policy)
        atomic_json(OUTPUTS/'threshold_summary.json',summary)
        print('\nKNOWN-ONLY DIAGNOSTIC COMPARISON — NO OFFICIAL THRESHOLD')
        print('Rule                         | Score cutoff | Margin cutoff | Correct kept | Rejected | Unknown FAR')
        # Display representative observed-data endpoints, not hand-picked cutoffs.
        middle_score=score_cuts[(len(score_cuts)-1)//2]
        middle_margin=margin_cuts[(len(margin_cuts)-1)//2]
        middle_rows=[r for r in evaluations if
            (r['rule']=='similarity_only' and r['min_similarity']==middle_score)
            or (r['rule']=='margin_only' and r['min_margin']==middle_margin)
            or (r['rule']=='similarity_and_margin' and r['min_similarity']==middle_score
                and r['min_margin']==middle_margin)]
        display=[evaluations[0],evaluations[1],evaluations[len(score_cuts)+1],*middle_rows,
                 evaluations[-1]]
        for r in display:
            score='disabled' if r['min_similarity'] is None else f"{r['min_similarity']:.12g}"
            margin='disabled' if r['min_margin'] is None else f"{r['min_margin']:.12g}"
            print(f"{r['rule']:28} | {score:12} | {margin:13} | {r['known_correct_accepted']:12}/{len(known)} | {r['known_rejected']:8} | NOT MEASURED")
        print(f"Full sweep: {len(evaluations)} rows; labelled unknown calibration samples: 0.")
        print('Official thresholds: NONE. Runtime policy: all identities remain unconfirmed.')
        print('Test audit only (excluded from optimization):',dict(applied_test),flush=True)
    finally:
        verify_originals(before)
        for path,digest in protected.items():
            if sha256(path)!=digest:
                raise RuntimeError(f'Protected artifact changed: {path}')
        print('PASS: registered embeddings, all previous outputs and DATASET unchanged.',flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    try:
        run()
    except Exception as exc:
        print(f'THRESHOLD EVALUATION ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(2)
