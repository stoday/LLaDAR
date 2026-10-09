"""Minimal real Streamlit chat UI; no external model or API endpoint."""
import streamlit as st

from agent import ask_agent

st.set_page_config(page_title="Streamlit 問答測試")
st.title("Streamlit 問答測試")
st.caption("本機固定回答測試，不呼叫外部模型。")
st.session_state.setdefault("messages", [])

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

if question := st.chat_input("請輸入問題"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    answer = ask_agent(question)
    with st.chat_message("assistant"):
        st.write(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
