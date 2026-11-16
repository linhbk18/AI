"""Gallery matching and conservative score/margin gating. No attendance writes.

An uncalibrated policy always leaves identity unconfirmed. Passing an evaluated
policy only produces a candidate for manual review, never identity confirmation.
"""
import hashlib
import json
import math

import numpy as np


def pipeline_signature(config, gallery_sha256):
    keys=('model_name','detector_backend','align','normalization','enforce_detection',
          'expand_percentage','anti_spoofing','max_faces','embedding_dimension',
          'input_preprocessing','packages','weights')
    payload={k:config[k] for k in keys}
    payload['gallery_sha256']=gallery_sha256
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def gate_match(best_candidate,best_similarity,second_candidate,second_similarity,
               policy=None,expected_pipeline_signature=None):
    """Apply score >= min_similarity AND margin >= min_margin (inclusive)."""
    first,second=float(best_similarity),float(second_similarity)
    if (not math.isfinite(first) or not math.isfinite(second) or
            not -1<=second<=first<=1 or best_candidate==second_candidate):
        raise ValueError('Expected finite sorted cosine scores for two distinct identities')
    result=dict(best_candidate=best_candidate,best_similarity=first,
                second_candidate=second_candidate,second_similarity=second,
                margin=first-second,identity_confirmed=False,manual_review_required=True,
                decision='unconfirmed',rejection_reasons=[],policy_applied=False)
    if not policy or policy.get('status')!='official' or policy.get('enabled') is not True:
        result['rejection_reasons']=['thresholds_not_calibrated_with_labelled_unknown_people']
        return result
    evidence=policy.get('evidence',{})
    if (not expected_pipeline_signature or policy.get('pipeline_signature')!=expected_pipeline_signature
            or not evidence.get('labelled_unknown_validation_samples',0)>0
            or not evidence.get('labelled_known_validation_samples',0)>0
            or evidence.get('independent_evaluation_completed') is not True
            or evidence.get('test_used_for_threshold_optimization') is not False):
        result['rejection_reasons']=['threshold_evidence_missing_or_pipeline_mismatch']
        return result
    score_cutoff,margin_cutoff=policy.get('min_similarity'),policy.get('min_margin')
    if (isinstance(score_cutoff,bool) or isinstance(margin_cutoff,bool)
            or not isinstance(score_cutoff,(float,int)) or not isinstance(margin_cutoff,(float,int))
            or not math.isfinite(score_cutoff) or not math.isfinite(margin_cutoff)
            or not -1<=score_cutoff<=1 or not 0<=margin_cutoff<=2):
        raise ValueError('Invalid official score/margin threshold configuration')
    reasons=[]
    if first<score_cutoff:
        reasons.append('similarity_below_evaluated_threshold')
    if result['margin']<margin_cutoff:
        reasons.append('identity_margin_below_evaluated_threshold')
    result.update(policy_applied=True,rejection_reasons=reasons,
                  decision='rejected' if reasons else 'candidate_passes_gates_manual_review_required')
    return result


class FaceMatcher:
    """Maximum sample cosine per student, then compare distinct student top two."""
    def __init__(self,records,config,gallery_sha256,policy=None):
        self.dimension=config['embedding_dimension']
        vectors=np.asarray([r['embedding'] for r in records],dtype=np.float64) if records else np.empty((0,self.dimension),dtype=np.float64)
        if vectors.ndim!=2 or vectors.shape[1]!=self.dimension or not np.isfinite(vectors).all():
            raise ValueError('Invalid gallery vectors')
        norms=np.linalg.norm(vectors,axis=1)
        if np.any(norms==0):
            raise ValueError('Zero-norm registered vector')
        self.matrix=vectors/norms[:,None] if len(vectors) else vectors
        self.records=records
        self.students=sorted({r['student_id'] for r in records})
        self.signature=pipeline_signature(config,gallery_sha256)
        self.policy=policy

    def match(self,embedding,allowed_students=None):
        vector=np.asarray(embedding,dtype=np.float64)
        if vector.shape!=(self.dimension,) or not np.isfinite(vector).all() or np.linalg.norm(vector)==0:
            raise ValueError('Invalid query embedding')
        if allowed_students is None:
            indices=list(range(len(self.records)))
        else:
            allowed=set(allowed_students)
            indices=[i for i,r in enumerate(self.records) if r['student_id'] in allowed]
        if not indices:return None
        selected=np.asarray(indices,dtype=np.int64)
        scores=np.clip(self.matrix[selected]@(vector/np.linalg.norm(vector)),-1.,1.)
        ranked=[]
        for student in sorted({self.records[i]['student_id'] for i in indices}):
            student_indices=[j for j,i in enumerate(indices) if self.records[i]['student_id']==student]
            best=max(student_indices,key=lambda j:float(scores[j]));record_index=indices[best]
            ranked.append((student,float(scores[best]),self.records[record_index]['source']))
        ranked.sort(key=lambda r:(-r[1],r[0]))
        if not ranked:return None
        if len(ranked)==1:
            first=ranked[0]
            return dict(best_candidate=first[0],best_similarity=first[1],second_candidate=None,second_similarity=None,margin=None,identity_confirmed=False,manual_review_required=True,decision='unconfirmed',nearest_registered_source=first[2])
        first,second=ranked[:2]
        result=gate_match(first[0],first[1],second[0],second[1],self.policy if allowed_students is None else None,self.signature)
        result['nearest_registered_source']=first[2]
        return result
