"""국토교통부 전월세 CSV (연립다세대 / 단독다가구) 로더."""
from pathlib import Path
from functools import lru_cache
import io
import pandas as pd
from file_mapping import DATA_FILES

DATA_DIR = Path(__file__).resolve().parent / 'data'


def _csv(path):
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    header = next((i for i, line in enumerate(lines) if line.startswith('"NO",') or line.startswith('NO,')), None)
    if header is None:
        raise ValueError(f'국토부 CSV 헤더를 찾지 못했습니다: {path.name}')
    return pd.read_csv(io.StringIO('\n'.join(lines[header:])), encoding='utf-8-sig', low_memory=False)


@lru_cache(maxsize=80)
def load_transactions(university, housing='연립·다세대'):
    """소형(3~15평), 신규 월세, 최근 CSV 포함 거래 반환."""
    if housing not in ('연립·다세대', '단독·다가구'):
        return pd.DataFrame()
    name = DATA_FILES[university][housing]
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f'{name} 파일이 없습니다.')
    df = _csv(path)
    area_col = '전용면적(㎡)' if housing == '연립·다세대' else '계약면적(㎡)'
    required = ['시군구', '전월세구분', '계약년월', '계약일', '보증금(만원)', '월세금(만원)', area_col]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'{name}에 필요한 열이 없습니다: {missing}')
    area = pd.to_numeric(df[area_col].astype(str).str.replace(',', '', regex=False), errors='coerce') / 3.305785
    deposit = pd.to_numeric(df['보증금(만원)'].astype(str).str.replace(',', '', regex=False), errors='coerce')
    rent = pd.to_numeric(df['월세금(만원)'].astype(str).str.replace(',', '', regex=False), errors='coerce')
    ym = df['계약년월'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(6)
    day = df['계약일'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(2)
    date = pd.to_datetime(ym + day, format='%Y%m%d', errors='coerce')
    mask = (df['전월세구분'].astype(str).str.strip() == '월세') & (rent > 0) & (deposit >= 0) & area.between(3, 15) & date.notna()
    if '계약구분' in df.columns:
        mask &= df['계약구분'].fillna('').astype(str).str.strip().isin(['신규', '-', ''])
    # 주택 유형의 단위가 달라 별도 유형으로 보관
    out = pd.DataFrame({'date':date[mask], 'area':area[mask], 'deposit':deposit[mask], 'rent':rent[mask],
                        'dong':df.loc[mask, '시군구'].astype(str).str.split().str[-1],
                        'housing':housing})
    return out.dropna().reset_index(drop=True)


def market_context(transactions, room, annual_rate, dong=None):
    if transactions is None or transactions.empty or room.get('housing') not in ('연립·다세대', '단독·다가구'):
        return None, pd.DataFrame()
    sample = transactions.loc[transactions['area'].between(float(room['area'])-2, float(room['area'])+2)].copy()
    if dong and dong != '전체':
        sample = sample.loc[sample['dong'] == dong].copy()
    if len(sample) < 8:
        return None, sample
    # 보증금의 기회비용을 기준 매물 보증금으로 맞춘 보정 월세
    sample['comparable_rent'] = sample['rent'] + (sample['deposit']-float(room['deposit']))*annual_rate/100/12
    target = float(room['rent'])
    score = 100 * ((sample['comparable_rent'] > target).sum() + .5*(sample['comparable_rent'] == target).sum())/len(sample)
    return {'count':len(sample),'median':float(sample['comparable_rent'].median()),'score':float(score)}, sample
