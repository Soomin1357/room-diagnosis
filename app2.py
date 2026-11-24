"""대학생 원룸 비교 진단기 (API 키 불필요).
설치: python -m pip install streamlit pandas folium streamlit-folium reportlab
실행: python -m streamlit run app2.py
지도 배경만 인터넷 연결이 필요합니다. 지도 끄기로 오프라인 계산 가능합니다.
"""
import math
import io
import json
import copy
from pathlib import Path
from xml.sax.saxutils import escape

from campus_config import CAMPUS_DATA, NEW_CAMPUSES
from coordinate_store import read_points, save_point


FLOORS = ['지상 2층 이상', '지상 1층', '반지하/지하', '옥탑']
CHECKS = {
    'mold': ('곰팡이·누수 흔적', '환기 상태와 비 온 뒤 벽·천장을 확인하세요.'),
    'noise': ('소음 문제', '낮과 밤의 실내 소음을 각각 확인하세요.'),
    'security': ('출입 보안·잠금장치 문제', '공동현관과 창문 잠금장치를 확인하세요.'),
    'vent': ('채광·환기 부족', '창문 개방과 낮 시간 채광을 확인하세요.'),
    'fees': ('관리비 내역 불명확', '포함 항목과 별도 청구 항목을 서면으로 확인하세요.'),
}
STATES = ['미확인', '문제 없음', '문제 있음']


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    v = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(max(0, min(1, v))))


def risks(room):
    items = []
    if room['floor'] == '반지하/지하':
        items.append(('구조 확인', '반지하/지하', '침수 흔적·배수·환기 상태를 확인하세요.'))
    if room['floor'] == '옥탑':
        items.append(('구조 확인', '옥탑', '지붕 방수와 냉난방 상태를 확인하세요.'))
    if room['floor'] == '지상 1층':
        items.append(('구조 확인', '1층', '창문 보안과 외부 시선을 확인하세요.'))
    for key, (label, advice) in CHECKS.items():
        state = room['checks'][key]
        if state != '문제 없음':
            items.append(('문제 있음' if state == '문제 있음' else '미확인', label, advice))
    return items


def evaluate(room, settings):
    """금액 단위: 만원. 보증금 반환 및 월 비용 고정을 가정한 비교 모델."""
    w = settings['weights']
    if sum(w) <= 0:
        raise ValueError('가중치 중 하나 이상은 0보다 커야 합니다.')
    extras = sum(v['estimate'] for v in room['utilities'].values() if not v['included'])
    monthly = room['rent'] + room['maintenance'] + extras
    opportunity = room['deposit'] * settings['rate'] / 100 / 12
    effective_area = room['area'] * {'지상 2층 이상': 1, '지상 1층': .92, '반지하/지하': .8, '옥탑': .88}[room['floor']]
    unit_cost = (monthly + opportunity) / effective_area
    # 정규분포 누적확률: 실제 시장 백분위가 아닌 예시 모델 점수입니다.
    z = (unit_cost-settings['benchmark']) / 2.0
    price = settings.get("market_price_score")
    if price is None:
        price = 50 * math.erfc(z / math.sqrt(2))
    d = distance_m(room['coords'], room.get('hub_coords') or CAMPUS_DATA[room['university']]['spots'][room['hub']])
    walk = room['manual_walk'] if room['manual_walk'] is not None else d * 1.25 / 80.0
    location = max(0, 100 - max(0, walk-5)*10)
    env = 50 + 15*room['elevator'] + 20*room['full_option']
    env += 15 if room['floor'] == FLOORS[0] else (-25 if room['floor'] in FLOORS[2:] else 0)
    env -= 10 * sum(room['checks'][k] == '문제 있음' for k in ['mold', 'noise', 'security', 'vent'])
    env = max(0, min(100, env))
    scores = [price, location, env]
    score = sum(s*x for s, x in zip(scores, w))/sum(w)
    n = settings['months']
    cash_total = monthly*n + room['oneoff']
    return dict(extra=extras, monthly=monthly, opportunity=opportunity,
                effective=monthly+opportunity, unit_cost=unit_cost, distance=d,
                walk=walk, scores=scores, score=score, cash_total=cash_total,
                economic_total=cash_total+opportunity*n,
                initial=room['deposit']+monthly+room['oneoff'],
                outflow=cash_total+room['deposit'], risks=risks(room))


def negotiate(room, settings, new_rent, new_deposit):
    trial = copy.deepcopy(room)
    trial.update(rent=new_rent, deposit=new_deposit)
    before, after = evaluate(room, settings), evaluate(trial, settings)
    return before, after, before['economic_total']-after['economic_total']


def ranking(rooms, settings, transactions=None, dong=None):
    rows = []
    for r in rooms:
        local_settings = dict(settings)
        if r.get('housing') in ('연립·다세대', '단독·다가구'):
            from real_estate import market_context
            from real_estate import load_transactions
            room_tx = transactions if r.get('housing') == settings.get('selected_housing') else load_transactions(r['university'], r['housing'])
            m, _ = market_context(room_tx,r,settings['rate'],dong)
            local_settings['market_price_score'] = m['score'] if m else None
        else:
            local_settings['market_price_score'] = None
        e = evaluate(r, local_settings)
        rows.append({'방 이름': r['name'], '종합 점수': round(e['score'], 2),
                     '월 지출(만원)': round(e['monthly'], 2),
                     '경제적 총비용(만원)': round(e['economic_total'], 2),
                     '통학(분)': round(e['walk'], 1),
                     '확인된 문제': sum(v == '문제 있음' for v in r['checks'].values()),
                     '미확인 항목': sum(v == '미확인' for v in r['checks'].values())})
    rows.sort(key=lambda r: (-r['종합 점수'], r['경제적 총비용(만원)'], r['방 이름']))
    previous, rank = None, 0
    for i, row in enumerate(rows, 1):
        if row['종합 점수'] != previous:
            rank = i
        previous = row['종합 점수']
        row['순위'] = rank
    return rows


def make_pdf(room, settings, rooms, new_rent, new_deposit, transactions=None, dong=None):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    # Windows 맑은 고딕 / 동봉 또는 리눅스 나눔고딕을 우선 사용합니다.
    candidates = [Path(__file__).parent/'NanumGothic.ttf', Path('C:/Windows/Fonts/malgun.ttf'),
                  Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')]
    font = 'HYSMyeongJo-Medium'
    for path in candidates:
        if path.exists():
            font = 'RoomKorean'
            pdfmetrics.registerFont(TTFont(font, str(path)))
            break
    else:
        pdfmetrics.registerFont(UnicodeCIDFont(font))
    normal = ParagraphStyle('ko', fontName=font, fontSize=10, leading=16, wordWrap='CJK', spaceAfter=6)
    heading = ParagraphStyle('head', parent=normal, fontSize=14, leading=21, spaceBefore=12, keepWithNext=True)
    title = ParagraphStyle('title', parent=normal, fontSize=21, leading=29, spaceAfter=16)
    def p(text, style=normal):
        return Paragraph(escape(str(text)), style)
    parts = [p('대학생 원룸 가성비 진단 보고서', title), p(room['name'], heading),
             p(f"{room['university']} / {room['housing']} / {room['floor']} / {room['area']}평"),
             p('실거래 표본이 충분하면 주택유형별 월세 실거래가로 가격 점수를 계산하고, 부족하면 예시 기준을 사용합니다. 통학 시간 직접 입력을 권장합니다.')]
    def table(rows, widths=None):
        t = Table([[p(v) for v in row] for row in rows], colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8eff8')),
                               ('VALIGN',(0,0),(-1,-1),'TOP'), ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cbd5e1')),
                               ('LEFTPADDING',(0,0),(-1,-1),8), ('RIGHTPADDING',(0,0),(-1,-1),8)]))
        parts.extend([t, Spacer(1, 10)])
    local_settings = dict(settings)
    if transactions is not None and not transactions.empty and room.get('housing') in ('연립·다세대', '단독·다가구'):
        from real_estate import market_context
        m, _ = market_context(transactions, room, settings['rate'], dong)
        local_settings['market_price_score'] = m['score'] if m else None
    e = evaluate(room, local_settings)
    parts.append(p('1. 평가 조건 및 결과', heading))
    parts.append(p(f"가중치(가격/거리/환경): {settings['weights']} / 기준 {settings['benchmark']:.2f}만원/평 / 연 기회비용률 {settings['rate']}%"))
    table([['항목', '결과'], ['종합 점수', f"{e['score']:.1f} / 100"],
           ['가격 / 거리 / 환경 점수', ' / '.join(f'{v:.1f}' for v in e['scores'])],
           ['월 실제 지출 / 기회비용 포함', f"{e['monthly']:.2f} / {e['effective']:.2f} 만원"],
           [f"통학 기준: {room['hub']}", f"{e['walk']:.1f}분 ({'직접 입력' if room['manual_walk'] is not None else '직선거리 기반 추정'}), 직선 {e['distance']:.0f}m"]], [190, 315])
    parts.append(p('2. 위험 요소 점검', heading))
    table([['분류', '항목', '확인 사항']]+[list(v) for v in e['risks']] if e['risks'] else [['분류', '결과'], ['입력 기준', '등록된 문제 없음. 안전을 보증하는 결과가 아닙니다.']])
    parts.append(p(f"3. 계약 {settings['months']}개월 총주거비", heading))
    table([['항목', '만원'], ['보증금 (반환 가정)', f"{room['deposit']:.2f}"],
           ['월세 / 관리비 / 별도 공과금', f"{room['rent']:.2f} / {room['maintenance']:.2f} / {e['extra']:.2f}"],
           ['일회성 비용', f"{room['oneoff']:.2f}"], ['순지출 (보증금 제외)', f"{e['cash_total']:.2f}"],
           ['경제적 총비용 (기회비용 포함)', f"{e['economic_total']:.2f}"],
           ['초기 필요자금 (보증금+첫 달+일회성)', f"{e['initial']:.2f}"]], [320,185])
    parts.append(p('4. 월세 협상 시뮬레이션', heading))
    _, after, saving = negotiate(room, settings, new_rent, new_deposit)
    parts.append(p(f"보증금 {room['deposit']} → {new_deposit}만원 / 월세 {room['rent']} → {new_rent}만원"))
    parts.append(p(f"계약 기간 경제적 비용 절감액: {saving:.2f}만원 / 협상 후 월 지출 {after['monthly']:.2f}만원. 음수는 비용 증가입니다."))
    parts.append(p('5. 저장한 매물 비교', heading))
    if rooms:
        rows = ranking(rooms, settings, transactions, dong)
        table([['순위', '방 이름', '점수', '월 지출', '총비용']]+[[r['순위'],r['방 이름'],r['종합 점수'],r['월 지출(만원)'],r['경제적 총비용(만원)']] for r in rows], [35,200,65,90,115])
    else:
        parts.append(p('저장한 비교 매물이 없습니다.'))
    parts.append(p('계산 기준: 월 지출=월세+관리비+미포함 공과금. 보증금 기회비용=보증금×연율÷12. 총비용은 월 비용 고정 및 보증금 전액 반환을 가정합니다. 대출이자는 별도로 모델링하지 않았습니다.'))
    parts.append(p('가격 점수는 실거래 자료가 있으면 실거래 기반, 부족하면 예시 평균과 표준편차 2만원의 정규분포 모델, 거리 점수는 5분 100점~15분 0점, 환경 점수는 시설·층수·입력 문제를 반영합니다. 미확인 항목은 별도로 표시됩니다.'))
    buf = io.BytesIO()
    def footer(canvas, doc):
        canvas.setFont(font, 9)
        canvas.drawRightString(550, 25, str(doc.page))
    SimpleDocTemplate(buf, pagesize=A4, rightMargin=45, leftMargin=45, topMargin=40, bottomMargin=45).build(parts, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def main():
    import streamlit as st
    import pandas as pd
    st.set_page_config(page_title='대학생 원룸 가성비 진단기', page_icon='🏠', layout='wide')
    st.markdown('''<style>
    .block-container {padding-top: 1.5rem; padding-bottom: 2.5rem; max-width: 1320px;}
    h1 {letter-spacing:-.04em; margin-bottom:.1rem;}
    h2,h3 {letter-spacing:-.025em;}
    [data-testid="stMetric"] {border:1px solid rgba(128,128,128,.20); border-radius:14px; padding:14px 16px;}
    [data-testid="stSidebar"] {border-right:1px solid rgba(128,128,128,.15);}
    div[data-testid="stTabs"] button {font-weight:600;}
    </style>''', unsafe_allow_html=True)
    st.title('🏠 대학생 원룸 가성비 진단기')
    st.caption('전국 30개 대학 · 월세 실거래 비교 · 원룸 점수 분석 · 단위: 만원')
    if 'rooms' not in st.session_state:
        st.session_state.rooms = []
    campus_points = read_points()
    with st.sidebar:
        st.header('① 원룸 기본 정보')
        university = st.selectbox('대학교', list(CAMPUS_DATA))
        info = CAMPUS_DATA[university]
        point_data = campus_points.get(university, {})
        if not point_data:
            st.error('이 대학의 좌표가 CSV에 없습니다.'); st.stop()
        point_names = list(point_data)
        default_hub = next((k for k,v in point_data.items() if v['default']),point_names[0])
        hub = st.selectbox('통학 기준점', point_names, index=point_names.index(default_hub), key='hub_'+university)
        name = st.text_input('매물 이름', '원룸 A', max_chars=80, help='비교 목록에서 구분할 이름을 입력하세요.').strip()
        housing = st.selectbox('주거 형태', ['연립·다세대', '단독·다가구', '오피스텔(실거래 비교 제외)', '기타(실거래 비교 제외)'])
        floor = st.selectbox('층수', FLOORS)
        deposit = st.number_input('보증금 (만원)', 0.0, 100000.0, 500.0, 50.0)
        rent = st.number_input('월세 (만원)', 0.0, 1000.0, 50.0, 1.0)
        maintenance = st.number_input('관리비 (만원)', 0.0, 300.0, 7.0, 1.0)
        area = st.number_input('면적 (평)', 0.5, 100.0, 6.0, 0.5, help='연립·다세대는 전용면적, 단독·다가구는 계약면적 기준으로 입력하세요.')
        st.divider()
        st.subheader('② 월 주거비')
        utilities = {}
        with st.expander('공과금 포함 여부와 예상액', expanded=False):
            for label, amount, included in [('수도',1.0,True),('인터넷',2.0,True),('가스·난방',4.0,False),('전기',3.0,False)]:
                inc = st.checkbox(label+' 관리비에 포함', included)
                estimate = st.number_input(label+' 미포함 시 월 예상액', 0.0, 100.0, amount, .5, disabled=inc)
                utilities[label] = dict(included=inc, estimate=estimate)
        elevator = st.checkbox('엘리베이터 있음')
        full_option = st.checkbox('풀옵션', True)
        oneoff = st.number_input('일회성 비용 합계 (이사·가구·중개 등)', 0.0, 10000.0, 50.0, 10.0)
        st.divider()
        st.subheader('③ 비교 설정')
        months = st.number_input('계약 기간 (개월)', 1, 120, 12)
        with st.expander('⚙️ 상세 평가 설정', expanded=False):
            rate = st.number_input('보증금 연 기회비용률 (%)', 0.0, 30.0, 4.5, .1)
            benchmark = st.number_input('평당 기준 비용 (만원)', .1, 100.0, float(info['avg_cost']), .1, key='benchmark_'+university)
            st.markdown('**평가 가중치**')
            w = [st.slider('가격 중요도',0,100,50), st.slider('거리 중요도',0,100,30), st.slider('환경 중요도',0,100,20)]
        if sum(w) == 0:
            st.error('중요도 중 하나 이상을 1 이상으로 설정하세요.')
            st.stop()
        st.caption('실제 적용 비율: '+ ' / '.join(f'{v/sum(w)*100:.1f}%' for v in w))
    settings = dict(months=months, rate=rate, benchmark=benchmark, weights=w, selected_housing=housing)
    from real_estate import load_transactions, market_context
    try:
        transactions = load_transactions(university, housing)
        st.sidebar.caption(f'실거래 비교 데이터 {len(transactions):,}건')
    except (FileNotFoundError, ValueError, UnicodeError) as exc:
        st.error(f'실거래 CSV 읽기 실패: {exc}')
        transactions = pd.DataFrame()
    dong_options = ['전체'] + (sorted(transactions['dong'].dropna().unique().tolist()) if not transactions.empty else [])
    selected_dong = st.sidebar.selectbox('실거래 비교 지역',dong_options,help='전체는 데이터에 포함된 지역 전체를 의미합니다.')
    loc_key = 'coords_'+university
    if loc_key not in st.session_state:
        st.session_state[loc_key] = info['default_pin']
    st.subheader('📍 위치와 통학 시간')
    st.caption(f'선택한 학교: {university}  |  통학 기준: {hub}')
    point = point_data[hub]
    with st.expander('🛠️ 대학 출입구 좌표 편집 (선택 사항)',expanded=False):
        st.caption('기존 기준점을 수정하거나 정문·후문을 새로 추가할 수 있습니다. 저장하면 campus_coordinates.csv에 반영됩니다.')
        point_name = st.text_input('수정/추가할 출입구 이름',value=hub,key='point_name_'+university+'_'+hub)
        hc1,hc2=st.columns(2)
        edit_lat=hc1.number_input('출입구 위도',33.0,39.0,float(point['coords'][0]),format='%.6f',key='edit_lat_'+university+'_'+hub)
        edit_lon=hc2.number_input('출입구 경도',124.0,132.0,float(point['coords'][1]),format='%.6f',key='edit_lon_'+university+'_'+hub)
        default_point=st.checkbox('이 출입구를 기본 통학 기준점으로 지정',value=point['default'],key='default_point_'+university+'_'+hub)
        if st.button('좌표를 CSV에 저장',key='save_point_'+university+'_'+hub):
            try:
                save_point(university,point_name,edit_lat,edit_lon,default_point)
                st.success('좌표가 campus_coordinates.csv에 저장되었습니다.')
                st.rerun()
            except (ValueError,OSError) as exc:
                st.error(f'좌표 저장 실패: {exc}')
        st.download_button('좌표 CSV 백업 다운로드',data=__import__('pathlib').Path(__file__).with_name('campus_coordinates.csv').read_bytes(),file_name='campus_coordinates.csv',mime='text/csv')
    hub_lat,hub_lon = point['coords']
    show_map = st.toggle('지도에서 원룸 위치 선택', False, help='지도를 클릭하면 원룸 좌표가 입력됩니다.')
    if show_map:
        try:
            import folium
            from streamlit_folium import st_folium
            m = folium.Map(location=st.session_state[loc_key], zoom_start=15)
            for spot, item in point_data.items():
                folium.Marker(item['coords'], tooltip=spot).add_to(m)
            folium.Marker((hub_lat, hub_lon), tooltip='선택한 통학 기준점', icon=folium.Icon(color='green')).add_to(m)
            folium.Marker(st.session_state[loc_key], tooltip='현재 매물', icon=folium.Icon(color='red')).add_to(m)
            result = st_folium(m, height=350, key='map_'+university, returned_objects=['last_clicked'])
            if result and result.get('last_clicked'):
                click = result['last_clicked']
                loc = (click['lat'],click['lng'])
                if loc == st.session_state.get('processed_click_'+university):
                    pass
                elif distance_m(loc, info['center']) > 5000:
                    st.warning('학교 중심 5km 이내를 선택하세요.')
                elif loc != st.session_state[loc_key]:
                    st.session_state['processed_click_'+university] = loc
                    st.session_state[loc_key] = loc
                    st.session_state['lat_'+university], st.session_state['lon_'+university] = loc
                    st.rerun()
        except ImportError:
            st.warning('지도를 사용하려면 folium과 streamlit-folium을 설치하세요. 좌표 입력은 계속 사용할 수 있습니다.')
    manual = st.toggle('통학 시간 직접 입력', True, help='직접 입력한 시간이 거리 점수에 우선 반영됩니다.')
    if manual:
        manual_walk = st.number_input('편도 통학 시간 (분)', 0.0, 300.0, 10.0, 1.0, help='지도 앱에서 확인한 도보 시간을 입력하세요.')
    else:
        manual_walk = None
        st.caption('예상 통학 시간 = 직선거리 × 1.25 ÷ 80m/분')
    with st.expander('원룸 좌표 직접 입력', expanded=False):
        c1,c2 = st.columns(2)
        lat = c1.number_input('위도', -90.0,90.0,st.session_state[loc_key][0],format='%.6f',key='lat_'+university)
        lon = c2.number_input('경도', -180.0,180.0,st.session_state[loc_key][1],format='%.6f',key='lon_'+university)
    st.session_state[loc_key] = (lat,lon)
    with st.expander('🛡️ 원룸 상태 체크리스트', expanded=False):
        checks = {}
        cols = st.columns(3)
        for i,(key,(label,_)) in enumerate(CHECKS.items()):
            checks[key] = cols[i%3].selectbox(label,STATES,key='check_'+key)
    room = dict(name=name, university=university, hub=hub, housing=housing, floor=floor,
                deposit=deposit, rent=rent, maintenance=maintenance, area=area, utilities=utilities,
                elevator=elevator, full_option=full_option, oneoff=oneoff, coords=(lat,lon),
                manual_walk=manual_walk, checks=checks, hub_coords=(hub_lat, hub_lon))
    market, sample = market_context(transactions,room,rate,None if selected_dong=='전체' else selected_dong) if not transactions.empty else (None,pd.DataFrame())
    settings['market_price_score'] = market['score'] if market else None
    e = evaluate(room, settings)
    st.subheader('📊 원룸 분석 결과')
    cols = st.columns(4)
    for col,label,value in zip(cols,['종합 점수','월 실제 지출','기회비용 포함 월비용','편도 통학 시간'],
                               [f"{e['score']:.1f}점",f"{e['monthly']:.2f}만원",f"{e['effective']:.2f}만원",f"{e['walk']:.1f}분"]):
        col.metric(label,value)
    if st.button('➕ 비교 목록에 매물 추가 / 갱신', type='primary', use_container_width=True):
        if not name:
            st.error('방 이름을 입력하세요.')
        else:
            st.session_state.rooms = [r for r in st.session_state.rooms if not (r['name']==name and r['university']==university)] + [copy.deepcopy(room)]
            st.success(f'{name} 저장 완료. 이름과 조건을 바꾸면 다른 방을 추가할 수 있습니다.')
    saved = [r for r in st.session_state.rooms if r['university']==university]
    tabs = st.tabs(['📊 진단 결과','🏆 매물 비교','💰 총주거비','🤝 월세 협상','📄 PDF·백업'])
    with tabs[0]:
        st.subheader(f'국토부 실거래가 비교 ({housing} · 신규 월세)')
        if market:
            a,b,c = st.columns(3)
            a.metric('유사 거래 수',f"{market['count']:,}건")
            b.metric('보증금 보정 월세 중앙값',f"{market['median']:.1f}만원")
            c.metric('가격 점수 (유사 거래 백분위)',f"{market['score']:.1f}점")
            st.caption('면적 ±2평, 선택 동(또는 구 전체), 신규 월세만 비교. 보증금 차이는 설정한 연 기회비용률로 보정하며 관리비는 제외합니다. 높은 점수일수록 상대적으로 저렴합니다.')
            trend = sample.assign(월=sample['date'].dt.to_period('M').astype(str)).groupby('월')['comparable_rent'].agg(['median','count']).reset_index()
            if not trend.empty:
                st.line_chart(trend.set_index('월')['median'])
                st.caption('월별 보증금 보정 월세 중앙값(만원). 거래 구성 차이 및 월별 표본 수에 유의하세요.')
                with st.expander('월별 표본 수'):
                    st.dataframe(trend.rename(columns={'median':'중앙값(만원)','count':'거래 건수'}),hide_index=True)
        else:
            st.warning('유사 거래 8건 미만이거나 CSV가 없어 기존 예시 가격 점수를 사용합니다. 이 경우 실제 시세 기반 평가가 아닙니다.')
        st.subheader('점수 구성')
        st.bar_chart(pd.DataFrame({'점수':e['scores']},index=['가격','거리','환경']))
        st.caption(f"보정 평당 비용 {e['unit_cost']:.2f}만원 / 예시 기준 {benchmark:.2f}만원. 직선거리 {e['distance']:.0f}m.")
        st.subheader('현장 확인 사항')
        if e['risks']:
            st.dataframe(pd.DataFrame(e['risks'],columns=['구분','항목','확인할 내용']),hide_index=True,use_container_width=True)
        else:
            st.success('입력 기준 등록된 문제 없음')

        with st.expander('분석 기준 및 계산 방법'):
            st.caption('시세 비교는 거래 데이터의 지역 범위를 기준으로 하며, 지도 좌표와 실제 이동 경로에 따라 거리 추정에 차이가 생길 수 있습니다.')
            st.write('가격: 현재 매물은 유사 실거래 보증금 보정 월세의 경험적 백분위 점수(표본 8건 이상), 부족하면 예시 기준 정규분포 점수입니다. 저장된 매물 순위는 아래 주의사항을 확인하세요.')
            st.write('거리: 직접 입력한 통학 시간을 우선 사용합니다. 자동 계산 시 직선거리 × 1.25 ÷ 80m/분을 적용합니다. 5분 이내 100점, 이후 분당 10점 감소, 15분 이상 0점.')
            st.write('환경: 기본 50점 + 엘리베이터 15점 + 풀옵션 20점 + 지상 2층 이상 15점. 반지하·옥탑은 25점 감점. 곰팡이·소음·보안·환기 문제는 각각 10점 감점. 0~100점 범위.')
            st.write('평당 비용 계산 시 층수 보정계수: 지상 2층 이상 1.0, 1층 0.92, 반지하 0.80, 옥탑 0.88. 모두 프로젝트용 가정입니다.')
    with tabs[1]:
        st.caption('현재 학교의 저장 매물만 비교합니다. 각 매물의 면적·보증금으로 실거래 가격 점수를 개별 계산합니다. 선택한 동은 모든 저장 매물에 공통 적용되며 실제 매물 소재 동과 다를 수 있습니다. 표본 부족 시 예시 기준으로 대체합니다.')
        if saved:
            rows = ranking(saved,settings,transactions,None if selected_dong=='전체' else selected_dong)
            st.dataframe(pd.DataFrame(rows)[['순위','방 이름','종합 점수','월 지출(만원)','경제적 총비용(만원)','통학(분)','확인된 문제','미확인 항목']],hide_index=True,use_container_width=True)
            winners = [r['방 이름'] for r in rows if r['순위']==1]
            st.success('현재 조건 1위: '+', '.join(winners))
            st.caption('표시 점수가 같으면 공동 순위입니다. 위험·미확인 항목도 함께 비교하세요.')
            delete = st.selectbox('삭제할 매물',[r['name'] for r in saved])
            if st.button('선택 매물 삭제'):
                st.session_state.rooms = [r for r in st.session_state.rooms if not(r['university']==university and r['name']==delete)]
                st.rerun()
        else:
            st.info('사이드바에서 조건을 입력하고 방을 2개 이상 저장하면 비교하기 편합니다.')
    with tabs[2]:
        cost_rows = []
        for n in sorted(set([6,12,24,months])):
            x = evaluate(room,dict(settings,months=n))
            cost_rows.append({'기간(개월)':n,'순지출(만원)':round(x['cash_total'],2),'기회비용(만원)':round(x['opportunity']*n,2),'경제적 총비용(만원)':round(x['economic_total'],2)})
        st.dataframe(pd.DataFrame(cost_rows),hide_index=True,use_container_width=True)
        st.write(f"선택한 {months}개월: 순지출 **{e['cash_total']:.2f}만원**, 경제적 총비용 **{e['economic_total']:.2f}만원**")
        st.write(f"초기 필요자금: **{e['initial']:.2f}만원** (보증금 + 첫 달 지출 + 일회성 비용)")
        st.write(f"계약 기간 누적 지급액: **{e['outflow']:.2f}만원** → 종료 시 보증금 **{deposit:.2f}만원** 반환 가정")
        st.caption('보증금 자체는 소모 비용에서 제외합니다. 공과금은 월평균 예상액이며 비용 고정·보증금 전액 반환을 가정합니다. 일회성 비용은 입주 시 지급으로 계산합니다.')
    with tabs[3]:
        st.subheader('보증금과 월세를 함께 바꾸어 비교')
        nc1,nc2 = st.columns(2)
        new_deposit = nc1.number_input('협상 후 보증금 (만원)',0.0,100000.0,float(deposit),50.0)
        new_rent = nc2.number_input('협상 후 월세 (만원)',0.0,1000.0,max(0.0,float(rent)-3),1.0)
        before,after,saving = negotiate(room,settings,new_rent,new_deposit)
        nc1.metric('매월 현금 지출 절감', f"{before['monthly']-after['monthly']:.2f}만원")
        nc2.metric(f'{months}개월 경제적 비용 절감', f'{saving:.2f}만원')
        st.write(f"추가로 필요한 보증금: **{new_deposit-deposit:+.2f}만원** / 협상 후 점수: **{after['score']:.1f}점**")
        break_even = rent - (new_deposit-deposit)*rate/100/12
        st.write(f'경제적 비용이 같아지는 월세: **{break_even:.2f}만원**')
        if break_even < 0:
            st.warning('이 보증금 조건에서는 월세를 0원으로 해도 기존 조건보다 경제적 비용이 높습니다.')
        st.caption('음수 절감액은 비용 증가입니다. 다른 조건은 동일하다고 가정하며, 협상 성사 여부나 시장 전환율을 예측하지 않습니다.')
    with tabs[4]:
        st.write('현재 방의 진단·위험 점검·총비용·협상 결과와 현재 학교의 저장 매물 순위를 PDF로 내려받습니다.')
        try:
            pdf = make_pdf(room,settings,saved,new_rent,new_deposit,transactions,None if selected_dong=='전체' else selected_dong)
            st.download_button('결과 PDF 다운로드',pdf,'room_report.pdf','application/pdf')
        except FileNotFoundError as err:
            st.error(str(err))
        except ImportError:
            st.error('PDF 기능에는 reportlab이 필요합니다: python -m pip install reportlab')
        st.caption('한글 PDF는 Windows 맑은 고딕 또는 내장 한글 CID 글꼴을 사용합니다.')
        st.download_button('저장 매물 JSON 백업',json.dumps({'version':1,'rooms':st.session_state.rooms},ensure_ascii=False,indent=2),'rooms_backup.json','application/json')
        uploaded = st.file_uploader('백업 불러오기 (기존 목록에 병합)',type=['json'])
        if uploaded and st.button('백업 복원'):
            try:
                if uploaded.size > 2_000_000:
                    raise ValueError('백업은 2MB 이하만 지원합니다.')
                data = json.loads(uploaded.getvalue())
                imported = validate_backup(data)
                merged = {(r['university'],r['name']):r for r in st.session_state.rooms}
                merged.update({(r['university'],r['name']):r for r in imported})
                st.session_state.rooms = list(merged.values())
                st.rerun()
            except (ValueError,KeyError,TypeError) as err:
                st.error(f'올바른 백업 파일이 아닙니다: {err}')
        st.caption('비교 목록은 현재 세션에 저장됩니다. 새로고침·서버 종료 전에 JSON으로 백업하세요. 공통 평가 기준은 사이드바에서 다시 설정합니다.')


def validate_backup(data):
    if not isinstance(data,dict) or data.get('version') != 1 or not isinstance(data.get('rooms'),list) or len(data['rooms'])>200:
        raise ValueError('지원하지 않는 형식 또는 매물 200개 초과')
    def number(v,lo,hi):
        return type(v) in (int,float) and math.isfinite(v) and lo<=v<=hi
    for r in data['rooms']:
        if not isinstance(r,dict):
            raise ValueError('매물 형식 오류')
        if not isinstance(r.get('name'),str) or not r['name'].strip() or len(r['name'])>80:
            raise ValueError('방 이름 오류')
        if r.get('university') not in CAMPUS_DATA or r.get('floor') not in FLOORS:
            raise ValueError('학교 또는 층수 오류')
        if r.get('hub') not in read_points().get(r['university'],{}) or not isinstance(r.get('housing'),str) or len(r['housing'])>80:
            raise ValueError('통학 거점 또는 주거 형태 오류')
        for k,lo,hi in [('deposit',0,100000),('rent',0,1000),('maintenance',0,300),('area',.5,100),('oneoff',0,10000)]:
            if not number(r.get(k),lo,hi):
                raise ValueError(k+' 값 오류')
        if not isinstance(r.get('coords'),(list,tuple)) or len(r['coords'])!=2 or not number(r['coords'][0],-90,90) or not number(r['coords'][1],-180,180):
            raise ValueError('좌표 오류')
        if 'hub_coords' in r and (not isinstance(r['hub_coords'],(list,tuple)) or len(r['hub_coords'])!=2 or not number(r['hub_coords'][0],-90,90) or not number(r['hub_coords'][1],-180,180)):
            raise ValueError('통학 기준점 좌표 오류')
        if 'manual_walk' not in r or (r['manual_walk'] is not None and not number(r['manual_walk'],0,300)):
            raise ValueError('통학 시간 오류')
        if any(type(r.get(k)) is not bool for k in ['elevator','full_option']):
            raise ValueError('시설 값 오류')
        if not isinstance(r.get('checks'),dict) or any(r['checks'].get(k) not in STATES for k in CHECKS):
            raise ValueError('점검 값 오류')
        if not isinstance(r.get('utilities'),dict) or set(r['utilities'])!={'수도','인터넷','가스·난방','전기'}:
            raise ValueError('공과금 항목 오류')
        for v in r['utilities'].values():
            if not isinstance(v,dict) or type(v.get('included')) is not bool or not number(v.get('estimate'),0,100):
                raise ValueError('공과금 값 오류')
    return data['rooms']


if __name__ == '__main__':
    main()
