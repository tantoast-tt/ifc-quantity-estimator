"""IFC Quantity Estimator — Streamlit UI"""
import io
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import ifcopenshell

from extract import run as extract_run
from estimate import load_prices, estimate, summarize_by_trade, to_excel
from viewer import figure as viewer_figure

SAMPLE = Path(__file__).parent / "samples" / "FZK-Haus.ifc"

st.set_page_config(page_title="IFC Quantity Estimator", layout="wide")
st.title("IFC Quantity Estimator")
st.caption("BIM(IFC) 모델 → 부재 물량 · 실별 면적 → 단가표 적용 → 개략 견적")

# ── 1. 입력 ───────────────────────────────────────────────
with st.sidebar:
    st.header("입력")
    up = st.file_uploader("IFC 파일 업로드", type=["ifc"])
    use_sample = st.checkbox("샘플(FZK-Haus) 사용", value=up is None)
    st.divider()
    st.markdown(
        "**사용 순서**\n1. IFC 업로드 또는 샘플 선택\n2. 물량·면적 확인\n"
        "3. 단가표 수정 → 견적 자동 재계산\n4. 엑셀 다운로드"
    )


@st.cache_data(show_spinner="IFC 파싱 중…")
def parse(file_bytes: bytes, name: str):
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    schema, el, summ, sp = extract_run(path, out_dir=tempfile.mkdtemp())
    return schema, el, summ, sp, path


@st.cache_resource(show_spinner="3D 형상 생성 중… (최초 1회, 10초 내외)")
def build_3d(path: str, name: str):
    f = ifcopenshell.open(path)
    _, el, _, _ = extract_run(path, out_dir=tempfile.mkdtemp())
    return viewer_figure(f, el)


if up is not None and not use_sample:
    schema, el, summ, sp, ifc_path = parse(up.getvalue(), up.name)
    src = up.name
elif SAMPLE.exists():
    schema, el, summ, sp, ifc_path = parse(SAMPLE.read_bytes(), SAMPLE.name)
    src = SAMPLE.name
else:
    st.info("IFC 파일을 업로드하세요.")
    st.stop()

st.success(f"**{src}** · {schema} · 부재 {len(el)}개 · 실 {len(sp)}개")

# ── 1-1. 3D 뷰 ───────────────────────────────────────────
st.subheader("3D 뷰")
st.caption("드래그로 회전, 휠로 확대. 범례의 분류를 클릭하면 숨겨집니다(지붕을 끄면 내부가 보입니다). 부재에 마우스를 올리면 면적·체적이 표시됩니다.")
show_3d = st.toggle("3D 뷰 표시", value=True)
if show_3d:
    st.plotly_chart(build_3d(ifc_path, src), width="stretch", config={"displayModeBar": False})

# ── 2. 물량 ───────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    st.subheader("부재 물량 요약")
    st.dataframe(summ, hide_index=True, width="stretch")
with c2:
    st.subheader("실별 면적")
    st.dataframe(sp, hide_index=True, width="stretch")
    st.metric("연면적(순)", f"{sp['순면적(㎡)'].sum():.2f} ㎡  /  {sp['평'].sum():.2f} 평")

with st.expander("부재 상세 (전체 목록)"):
    st.dataframe(el, hide_index=True, width="stretch")

# ── 3. 단가표 ─────────────────────────────────────────────
st.subheader("단가표 (수정 가능)")
st.caption("단가는 가정값입니다. 셀을 더블클릭해 수정하면 견적이 즉시 재계산됩니다.")
if "prices" not in st.session_state:
    st.session_state.prices = load_prices()
prices = st.data_editor(
    st.session_state.prices,
    hide_index=True, width="stretch", num_rows="fixed",
    column_config={
        "단가(원)": st.column_config.NumberColumn(min_value=0, step=1000, format="%d"),
        "산출기준": st.column_config.TextColumn(disabled=True),
    },
    key="price_editor",
)

# ── 4. 견적 ───────────────────────────────────────────────
est = estimate(summ, sp, prices)
by, kpi = summarize_by_trade(est, sp)

st.subheader("견적")
k1, k2, k3, k4 = st.columns(4)
k1.metric("총액", f"{kpi['총액(원)']:,} 원")
k2.metric("평당", f"{kpi['평당(원)']:,} 원")
k3.metric("㎡당", f"{kpi['㎡당(원)']:,} 원")
k4.metric("연면적", f"{kpi['연면적(평)']} 평")

c3, c4 = st.columns([3, 2])
with c3:
    st.dataframe(
        est.style.format({"단가(원)": "{:,}", "금액(원)": "{:,}", "수량": "{:,.2f}"}),
        hide_index=True, width="stretch",
    )
with c4:
    fig = px.bar(by, x="공종", y="금액(원)", text="비중(%)", title="공종별 금액")
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    fig.update_layout(yaxis_tickformat=",", margin=dict(t=40, b=0))
    st.plotly_chart(fig, width="stretch")

# ── 5. 다운로드 ───────────────────────────────────────────
buf = io.BytesIO()
to_excel(buf, summ, sp, est, by, kpi)
st.download_button(
    "엑셀 다운로드 (요약·공종별·견적내역·부재물량·실별면적)",
    data=buf.getvalue(),
    file_name=f"estimate_{Path(src).stem}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
