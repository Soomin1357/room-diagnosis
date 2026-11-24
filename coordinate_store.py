"""대학 출입구 좌표를 UTF-8-SIG CSV로 관리. 외부 API 없음."""
import csv
from pathlib import Path

PATH = Path(__file__).resolve().parent / 'campus_coordinates.csv'
FIELDS = ['university','point_name','latitude','longitude','is_default','status','source']

def read_points():
    points = {}
    with PATH.open('r',encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            university = row['university'].strip()
            name = row['point_name'].strip()
            lat, lon = float(row['latitude']), float(row['longitude'])
            if not university or not name or not (33 <= lat <= 39 and 124 <= lon <= 132):
                raise ValueError(f'좌표 CSV의 학교/기준점/한국 좌표를 확인하세요: {university} {name}')
            points.setdefault(university,{})[name] = dict(coords=(lat,lon),default=row['is_default'].strip()=='1',status=row['status'],source=row['source'])
    return points

def save_point(university,name,lat,lon,make_default=True,source='지도에서 직접 확인'):
    if not name.strip() or not (33 <= lat <= 39 and 124 <= lon <= 132):
        raise ValueError('기준점 이름과 한국 내 위도/경도를 입력하세요.')
    with PATH.open('r',encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    if make_default:
        for row in rows:
            if row['university']==university:
                row['is_default']='0'
    found=False
    for row in rows:
        if row['university']==university and row['point_name']==name.strip():
            row.update(latitude=f'{lat:.6f}',longitude=f'{lon:.6f}',status='사용자 입력(미검증)',source=source,is_default='1' if make_default else row['is_default'])
            found=True
            break
    if not found:
        rows.append(dict(university=university,point_name=name.strip(),latitude=f'{lat:.6f}',longitude=f'{lon:.6f}',is_default='1' if make_default else '0',status='사용자 입력(미검증)',source=source))
    tmp=PATH.with_suffix('.tmp')
    with tmp.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader();writer.writerows(rows)
    tmp.replace(PATH)
