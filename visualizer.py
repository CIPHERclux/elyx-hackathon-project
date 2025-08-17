import streamlit as st
import pandas as pd
import json
from datetime import datetime
import re

# --- Helper Functions ---

def parse_date_from_line(line):
    """Parses a datetime object from a diary line."""
    match = re.search(r"\[(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2}\s[AP]M)\]", line)
    if match:
        try:
            return datetime.strptime(match.group(1), '%m/%d/%y, %I:%M %p')
        except ValueError:
            return None
    return None

def parse_diary_to_events(diary_content):
    """
    Parses the diary text to extract multiple types of timeline events.
    """
    timeline_events = []
    
    # --- Stage 1: Group lines into conversations ---
    conversations = []
    current_conversation = []
    conversation_regex = re.compile(r"\[(\d{1,2}/\d{1,2}/\d{2,4}, \d{1,2}:\d{2}\s[AP]M)\]\s(.*?):\s(.*)", re.DOTALL)
    
    for line in diary_content.splitlines():
        match = conversation_regex.match(line)
        if match:
            speaker = match.group(2).strip()
            if speaker == "Rohan" and current_conversation:
                conversations.append(current_conversation)
                current_conversation = []
            current_conversation.append(line)
    if current_conversation:
        conversations.append(current_conversation)

    # --- Stage 2: Process each conversation to generate events ---
    for convo_group in conversations:
        first_line = convo_group[0]
        event_date = parse_date_from_line(first_line)
        if not event_date:
            continue

        # Create the main conversation event
        is_question = any('?' in line for line in convo_group if ": Rohan:" in line)
        title = "Member asked a question" if is_question else "Conversation"
        timeline_events.append({
            'date': event_date, 'type': '💬 Conversation', 'title': title, 'data': convo_group
        })

        # Look for other event types within the conversation text
        full_convo_text = "\n".join(convo_group)
        
        # Infer Plan Updates
        if re.search(r"(Rachel|Carla|Advik):\s.*(plan|diet|exercise|routine|protocol|update)", full_convo_text, re.IGNORECASE):
            if re.search(r"(adjust|update|new|change|tweak|add)", full_convo_text, re.IGNORECASE):
                 timeline_events.append({
                    'date': event_date, 'type': '📅 Plan Update', 'title': "Plan change discussed", 'data': convo_group
                })

        # Infer Travel
        if re.search(r"Rohan:\s.*(travel|trip|flying|jet-lagged|on the road|whirlwind)", full_convo_text, re.IGNORECASE):
            timeline_events.append({
                'date': event_date, 'type': '✈️ Travel', 'title': "Member mentioned travel", 'data': convo_group
            })

        # Extract KPI Mentions
        kpi_match = re.search(r"LDL (is|is still|was) (\d+)", full_convo_text, re.IGNORECASE)
        if kpi_match:
            timeline_events.append({
                'date': event_date, 'type': '📈 KPI Update', 'title': f"LDL level reported: {kpi_match.group(2)}", 'data': convo_group
            })
            
        # Extract Logged Actions/Decisions
        action_match = re.search(r"ACTION:\s*({.*})", full_convo_text)
        if action_match:
            try:
                action_data = json.loads(action_match.group(1))
                reason = action_data.get('reason', 'N/A')
                title = f"Decision: {action_data.get('type', 'Action')} ({reason})"
                timeline_events.append({
                    'date': event_date, 'type': '✅ Decision Logged', 'title': title, 'data': convo_group
                })
            except json.JSONDecodeError:
                pass # Ignore malformed JSON

    return timeline_events


# --- Main Streamlit App ---

def main():
    st.set_page_config(layout="wide", page_title="Elyx Diary Visualizer")

    st.title("Elyx Diary Visualizer")
    st.markdown("This tool reconstructs the member's journey using **only the conversation diary**. It infers key events to create a comprehensive timeline.")

    # Simplified File Uploader
    diary_file = st.file_uploader("Upload Member Diary TXT File (e.g., run15_diary.txt)", type="txt")

    if diary_file is not None:
        try:
            diary_content = diary_file.getvalue().decode("utf-8")
        except Exception as e:
            st.error(f"Error reading file: {e}")
            return

        # --- Information Extraction from Diary ---
        member_name_match = re.search(r":\s(Hi|Hey)\s(Rohan)", diary_content)
        member_name = member_name_match.group(2) if member_name_match else "Member"
        
        condition_match = re.search(r"(hypertension|high bp|high blood pressure)", diary_content, re.IGNORECASE)
        condition = "Hypertension" if condition_match else "Unavailable"


        st.header(f"Inferred Member Snapshot: {member_name}")
        col1, col2, col3 = st.columns(3)
        col1.metric("Name", member_name)
        col2.metric("Inferred Condition", condition)
        col3.metric("Data Source", "Diary TXT Only")
        

        # --- Timeline Creation from Diary ---
        st.header("Member Journey Timeline")
        
        all_events = parse_diary_to_events(diary_content)
        
        # Sort all events chronologically
        # We use a secondary sort key on a custom order to group related events
        type_order = {'💬 Conversation': 0, '📈 KPI Update': 1, '📅 Plan Update': 2, '✈️ Travel': 3, '✅ Decision Logged': 4}
        sorted_events = sorted(all_events, key=lambda x: (x['date'], type_order.get(x['type'], 99)))

        # --- Display Timeline ---
        if not sorted_events:
            st.warning("No timeline events could be processed from the diary file.")
        else:
            last_date = None
            for event in sorted_events:
                current_date = event['date'].date()
                if current_date != last_date:
                    st.subheader(f"🗓️ {current_date.strftime('%B %d, %Y')}")
                    last_date = current_date
                
                expander_title = f"{event['type']}: {event['title']}"
                with st.expander(expander_title):
                    st.markdown(f"**Triggered during conversation at {event['date'].strftime('%I:%M %p')}**")
                    st.markdown("---")
                    
                    # Display the full conversation context for any inferred event
                    for line in event['data']:
                        match = re.match(r"\[.*?\]\s(.*?):\s(.*)", line, re.DOTALL)
                        if match:
                            speaker, text = match.groups()
                            if speaker.strip() == member_name:
                                st.markdown(f"> **{speaker}:** {text}")
                            else:
                                st.markdown(f"**{speaker} (Elyx):** {text}")

if __name__ == "__main__":
    main()