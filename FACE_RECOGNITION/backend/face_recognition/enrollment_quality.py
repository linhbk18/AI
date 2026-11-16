"""Configurable, conservative checks for transient student enrollment frames."""
import math


MIN_CONFIDENCE=.60
MIN_FACE_FRACTION=.20
MIN_FACE_PIXELS=120
MAX_FACE_FRACTION=.72
MIN_LAPLACIAN_VARIANCE=60.0
MIN_BRIGHTNESS=70.0
MAX_BRIGHTNESS=200.0


def face_box_feedback(width,height,x,y,face_width,face_height,confidence):
    if confidence<MIN_CONFIDENCE:return 'Chưa nhận rõ khuôn mặt. Hãy nhìn vào camera.'
    if face_width<max(MIN_FACE_PIXELS,MIN_FACE_FRACTION*width):return 'Lại gần hơn.'
    if face_width>MAX_FACE_FRACTION*width:return 'Lùi ra xa.'
    margin=max(2,int(.015*min(width,height)))
    if x<margin or y<margin or x+face_width>width-margin or y+face_height>height-margin:
        return 'Lùi ra xa để khuôn mặt nằm trọn trong khung.'
    return None


def frame_quality_feedback(laplacian_variance,mean_brightness):
    if laplacian_variance<MIN_LAPLACIAN_VARIANCE:return 'Giữ yên đầu để hình ảnh rõ hơn.'
    if not MIN_BRIGHTNESS<=mean_brightness<=MAX_BRIGHTNESS:
        return 'Hãy điều chỉnh ánh sáng để khuôn mặt không quá tối hoặc quá sáng.'
    return None


def estimate_head_pose(left_eye,right_eye,nose_tip,left_mouth,right_mouth):
    eye_mid=((left_eye[0]+right_eye[0])/2,(left_eye[1]+right_eye[1])/2)
    mouth_mid=((left_mouth[0]+right_mouth[0])/2,(left_mouth[1]+right_mouth[1])/2)
    eye_span=max(1,abs(left_eye[0]-right_eye[0]))
    yaw=math.degrees(math.atan2(nose_tip[0]-eye_mid[0],eye_span*.42))
    vertical=max(1,mouth_mid[1]-eye_mid[1])
    nose_ratio=(nose_tip[1]-eye_mid[1])/vertical
    pitch=(.52-nose_ratio)*110
    return float(yaw),float(pitch)


def posture_matches(posture,yaw,pitch):
    return {'thẳng':abs(yaw)<=12 and abs(pitch)<=12,
            'quay_trái':20<=yaw<=45 and abs(pitch)<=20,
            'quay_phải':-45<=yaw<=-20 and abs(pitch)<=20,
            'ngẩng_nhẹ':12<=pitch<=30 and abs(yaw)<=20,
            'cúi_nhẹ':-30<=pitch<=-12 and abs(yaw)<=20}.get(posture,False)


def classify_posture(yaw, pitch):
    if abs(yaw) <= 15 and abs(pitch) <= 15:
        return 'thẳng'
    if 18 <= yaw <= 50 and abs(pitch) <= 25:
        return 'quay_trái'
    if -50 <= yaw <= -18 and abs(pitch) <= 25:
        return 'quay_phải'
    if 10 <= pitch <= 35 and abs(yaw) <= 25:
        return 'ngẩng_nhẹ'
    if -35 <= pitch <= -10 and abs(yaw) <= 25:
        return 'cúi_nhẹ'
    return None


def pose_guidance_feedback(yaw, pitch):
    if classify_posture(yaw, pitch):
        return None
    if abs(pitch) > 35:
        return 'Góc ngẩng hoặc cúi quá lớn. Hãy nhìn về hướng camera.'
    if abs(yaw) > 55:
        return 'Góc quay đầu quá xa. Hãy quay mặt dần về phía trước.'
    if 0 < yaw < 18:
        return 'Quay đầu sang trái thêm một chút nữa.'
    if -18 < yaw < 0:
        return 'Quay đầu sang phải thêm một chút nữa.'
    if 0 < pitch < 10:
        return 'Ngẩng đầu lên thêm một chút nữa.'
    if -10 < pitch < 0:
        return 'Cúi đầu xuống thêm một chút nữa.'
    return 'Hãy giữ yên đầu và điều chỉnh theo hướng dẫn.'

