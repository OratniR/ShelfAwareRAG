import pandas as pd
import streamlit as st

from shelf_aware.database import InventoryDAO

# --- 設定 ---
st.set_page_config(page_title="ShelfAware", page_icon="📦", layout="wide")

# CSS調整
st.markdown(
    """
    <style>
    .main .block-container { padding-top: 2rem; }
    h1 { font-size: 1.8rem !important; }
    div[data-testid="stDataEditor"] table { margin-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# DAO初期化
if "dao" not in st.session_state:
    st.session_state.dao = InventoryDAO(use_chroma=False)

# --- タイトル ---
st.title("📦 ShelfAware")


# --- データ取得 & 実データ計算ロジック ---
def get_inventory_df():
    # デフォルトは日付順で取得
    items = st.session_state.dao.get_all_items(sort_by_date=True)
    if not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)

    # 1. 賞味期限 (expiry_date) の変換
    if "expiry_date" in df.columns:
        df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    else:
        df["expiry_date"] = pd.NaT

    # 2. 登録日/更新日 (updated_at) の変換
    if "updated_at" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], errors="coerce")

    # 3. 残り日数の計算 (表示用)
    today = pd.Timestamp.now().normalize()
    df["days_left"] = (df["expiry_date"] - today).dt.days

    # 4. 削除チェックボックス用の列を初期化
    df.insert(0, "削除", False)

    return df


# データをロード
df = get_inventory_df()

if df.empty:
    st.info("データがありません。Siriでアイテムを追加してください。")
else:
    # --- 1. アクションエリア（期限切れ間近のみ） ---
    urgent_items = df[(df["days_left"].notna()) & (df["days_left"] <= 7)].sort_values("days_left")

    if not urgent_items.empty:
        st.subheader("⚠️ 早く使いましょう")
        cols = st.columns(min(len(urgent_items), 3))
        for idx, (_, row) in enumerate(urgent_items.iterrows()):
            if idx < 3:
                with cols[idx]:
                    days = int(row["days_left"])
                    if days < 0:
                        msg = f"🔥 {abs(days)}日 期限切れ"
                    elif days == 0:
                        msg = "⚡️ 今日まで！"
                    else:
                        msg = f"⏳ 残り {days} 日"

                    st.error(f"**{row['id']}**\n\n📍 {row['location']}\n\n{msg}")

    st.divider()

    # --- 2. 検索 & 編集リスト ---
    col_header, col_search = st.columns([2, 1])
    with col_header:
        st.write("📝 **在庫リスト (ヘッダーをクリックしてソート可能)**")
    search_q = col_search.text_input("🔍 検索", placeholder="アイテム名...")

    # 検索フィルタ
    if search_q:
        df = df[df.apply(lambda row: search_q.lower() in row.astype(str).str.lower().values, axis=1)]

    # 表示・編集するカラムの定義（updated_at を追加）
    display_cols = ["削除", "id", "location", "expiry_date", "is_estimated", "updated_at"]

    # --- エディタ本体 ---
    edited_df = st.data_editor(
        df[display_cols],
        column_config={
            "削除": st.column_config.CheckboxColumn("削除", width="small", default=False),
            "id": st.column_config.TextColumn("アイテム名", width="medium", disabled=True),
            "location": st.column_config.TextColumn("場所", width="small"),
            "expiry_date": st.column_config.DateColumn("賞味期限", width="medium", format="YYYY-MM-DD"),
            "is_estimated": st.column_config.SelectboxColumn(
                "ステータス",
                width="medium",
                options=[0, 1, 2],
                format_func=lambda x: {0: "🕒 未処理", 1: "✅ 推定済", 2: "🚫 対象外"}.get(x, str(x)),
                required=True,
            ),
            # 【追加】登録日（更新日）の設定
            "updated_at": st.column_config.DatetimeColumn(
                "登録日",
                format="MM/DD HH:mm",  # 見やすいように年月日時分だけ表示
                width="medium",
                disabled=True,  # 自動更新されるものなので編集不可にする
            ),
        },
        use_container_width=True,
        hide_index=True,
        key="inventory_editor",
    )

    # --- 3. 保存ボタン ---
    col_save, _ = st.columns([1, 4])

    if col_save.button("💾 変更を保存 & 削除を実行", type="primary"):
        updated_count = 0
        deleted_count = 0

        progress_bar = st.progress(0)
        total_rows = len(edited_df)

        for idx, row in edited_df.iterrows():
            item_id = row["id"]

            # A. 削除チェックがある場合 -> 削除
            if row["削除"]:
                st.session_state.dao.delete_item(item_id)
                deleted_count += 1

            # B. 更新処理
            else:
                e_date = row["expiry_date"]
                date_str = None
                if pd.notnull(e_date):
                    date_str = e_date.strftime("%Y-%m-%d")

                st.session_state.dao.update_item_state(
                    item_id=item_id, expiry_date=date_str, is_estimated=int(row["is_estimated"])
                )
                updated_count += 1

            progress_bar.progress((idx + 1) / total_rows)

        # 完了メッセージ
        msg = []
        if updated_count > 0:
            msg.append(f"{updated_count}件を更新")
        if deleted_count > 0:
            msg.append(f"{deleted_count}件を削除")

        if msg:
            st.success(f"完了しました: {'、'.join(msg)}")
            st.rerun()
        else:
            st.info("変更はありませんでした")
