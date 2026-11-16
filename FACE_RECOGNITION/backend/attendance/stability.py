"""Server-side 5-of-7 observations; preview frames never count as biometric evidence."""
REQUIRED = 5
WINDOW = 7


def observe(previous, student_id, sequence):
    if previous.get('sequence') != sequence - 1:
        previous = {}
    identity = previous.get('student_id')
    samples = list(previous.get('samples', []))
    if student_id is not None and identity is not None and student_id != identity:
        samples = []
    identity = student_id if student_id is not None else identity
    samples = (samples + [student_id])[-WINDOW:]
    count = sum(value == identity for value in samples) if identity is not None else 0
    state = dict(student_id=identity, sequence=sequence, samples=samples)
    ready = student_id is not None and len(samples) == WINDOW and count >= REQUIRED
    return state, dict(observed=count, required=REQUIRED, window=WINDOW,
                       collected=len(samples), ready=ready)
