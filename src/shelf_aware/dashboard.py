import streamlit as st
import pandas as pd
import random
from shelf_aware.database import InventoryDAO
from datetime import datetime, timedelta

# ページ設定
st.set_page_config(page_title="ShelfAware", page_icon="📦", layout="wide")

# スタイル調整（スマホで見やすく）
st.markdown("""
    <style>
    .main .block-container { padding-top: 2rem; }
    h1 { font-size: 1.8rem !important; }
    </style>
    """, unsafe_allow_html=True)

if 'dao' not in st.session_state:
    st.session_state.dao = InventoryDAO()

# --- タイトル（シンプルに） ---
st.title("📦 ShelfAware")

# --- データ読み込み & ダミーデータ生成 ---
def get_data_with_dummy_expiry():
    items = st.session_state.dao.get_all_items()
    if not items:
        return pd.DataFrame()
    
    df = pd.DataFrame(items)
    # updated_at の整形
    df['updated_at'] = pd.to_datetime(df['updated_at'])
    
    # 【ダミーロジック】表示確認用に「残り日数」をランダム生成
    # 実際には Phase 2 で DB から expiry_date を取得します
    df['days_left'] = [random.randint(1, 60) for _ in range(len(df))]
    
    # わざとらしく「危険なアイテム」を数個作る（デモ用）
    if len(df) > 0:
        df.loc[0, 'days_left'] = 2  # あと2日
        if len(df) > 1:
            df.loc[1, 'days_left'] = 5  # あと5日
            
    return df

df = get_data_with_dummy_expiry()

if df.empty:
    st.info("データがありません。Siriでアイテムを追加してください。")
else:
    # --- 1. アクションエリア（最優先事項） ---
    # 賞味期限が近いもの（7日以内）を抽出
    urgent_items = df[df['days_left'] <= 7].sort_values('days_left')
    
    if not urgent_items.empty:
        st.subheader("⚠️ 早く使いましょう")
        # 横並びのカラムでカード風に表示
        cols = st.columns(len(urgent_items) if len(urgent_items) < 3 else 3)
        for idx, (_, row) in enumerate(urgent_items.iterrows()):
            # 3つまで表示
            if idx < 3:
                with cols[idx]:
                    # 赤枠のアラート表示
                    st.error(
                        f"**{row['id']}**\n\n"
                        f"📍 {row['location']}\n\n"
                        f"⏳ 残り {row['days_left']} 日目安"
                    )
    else:
        st.success("🎉 現在、賞味期限切れ間近のアイテムはありません！")

    # --- 2. 検索 & 全リスト ---
    st.divider()
    col_search, col_sort = st.columns([2, 1])
    search_q = col_search.text_input("🔍 アイテムを探す", placeholder="アイテム名、場所...")
    
    # フィルタリング
    if search_q:
        df = df[df.apply(lambda row: search_q.lower() in row.astype(str).str.lower().values, axis=1)]

    # 表示用データフレームの整形
    display_df = df.copy()
    display_df['更新日'] = display_df['updated_at'].dt.strftime('%m/%d')
    
    # カラム並び替え: アイテム名を一番左に
    display_df = display_df[['id', 'location', 'days_left', 'updated_at']]
    display_df = display_df.rename(columns={
        'id': 'アイテム名',
        'location': '場所'
    })

    # テーブル表示（文字色などはStreamlit標準に任せて視認性確保）
    st.dataframe(
        display_df,
        column_config={
            "アイテム名": st.column_config.TextColumn("アイテム名", width="medium"),
            "場所": st.column_config.TextColumn("場所", width="small"),
            "days_left": st.column_config.ProgressColumn(
                "期限目安",
                help="賞味期限までの残り日数",
                format="%d 日",
                min_value=0,
                max_value=30,  # 30日以下でバーが動き出すイメージ
            ),
            "updated_at": st.column_config.DatetimeColumn("最終確認", format="M/D")
        },
        use_container_width=True,
        hide_index=True
    )

    # --- 3. クイック削除 ---
    with st.expander("🗑️ 使い切ったアイテムを消す"):
        to_delete = st.selectbox("アイテムを選択", df['id'].unique(), key="delete_box")
        if st.button("削除実行"):
            st.session_state.dao.delete_item(to_delete)
            st.toast(f"「{to_delete}」を削除しました")
            st.rerun()