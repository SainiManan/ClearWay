import streamlit as st

st.set_page_config(
    page_title="SafeStride",
    page_icon="🌙",
)

st.title("SafeStride 🌙")
st.subheader("Safer Route Recommendations")

st.write(
    "Compare walking routes using available "
    "safety indicators."
)

start = st.text_input("Starting location")
destination = st.text_input("Destination")

if st.button("Find routes"):
    if start.strip() and destination.strip():
        st.info(
            f"Route search: {start} → {destination}"
        )
        st.write(
            "Route calculation will be integrated next."
        )
    else:
        st.warning("Enter both locations.")