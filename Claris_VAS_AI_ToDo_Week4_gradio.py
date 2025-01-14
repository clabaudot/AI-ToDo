# Install necessary packages , just ONE TIME, comment after.

#%pip install --upgrade openai
#%pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
#%pip install --upgrade gradio

# Import Libraries
import random, openai, json, os
import pandas as pd
#from google.colab import userdata
from pydantic import BaseModel
from typing import List, Literal
from datetime import time
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import os.path
from datetime import datetime, timedelta
import subprocess

# Gradio
import gradio as gr
# If you would like to use openai,
# please define the openai_key below otherwise leave as None
#openai_key = userdata.get('openaikey')

# At the top of the file, after imports
def get_openai_key():
    """Get OpenAI API key with detailed error checking"""

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        # Try alternative environment variable name
        api_key = os.environ.get('MYOPENAIKEY')
      
    if not api_key:
        print("Environment variables available:", list(os.environ.keys()))
        raise ValueError("OpenAI API key not found in environment variables. "
                        "Please ensure OPENAI_API_KEY or MYOPENAIKEY is set.")
    return api_key

# Replace the existing openai_key check with:
try:
    openai_key = get_openai_key()
except ValueError as e:
    print(f"Warning: {str(e)}")

try:
    from config import OPENAI_API_KEY
    os.environ['OPENAI_API_KEY'] = OPENAI_API_KEY
except ImportError:
    # Fall back to environment variable
    pass

# Get Input Data
# Example of input data. Format is a list of text
input_data = """
('bike ride', 'pay electricity bill', 'decorate house for christmas', 'clean the bathroom', 'call my friend Linda')
"""

# Get Output Data
# Example of output data. Format is a dictionary or json file
output_data = """
list of tasks (and maybe subtasks) with an estimated duration, a category, a difficulty level, an indicator if it's inside or outside, an indicator if it requires travelling, a suggested time in the week to do it.
"""

# Add these models at the top of the file, after imports
# Structured data
class TodoTask(BaseModel):
    task_ID: str
    task_name: str
    estimated_duration: int
    category: str
    difficulty_level: Literal["easy", "medium", "difficult"]
    ind_outside: bool
    ind_travel: bool
    status: Literal["not started", "done", "partially done", "reschedule please", "expand please"]
    actual_duration: int
    estimated_remaining_duration: int
    has_subtasks: bool
    subtasks: List["TodoTask"] = None

class TodoTaskList(BaseModel):
    tasks: List[TodoTask]

class TaskInCalendar(BaseModel):
    task: TodoTask
    start_date: str  # Use ISO 8601 format (e.g., "2024-12-28")
    end_date: str    # Use ISO 8601 format
    start_time: str  # Use "HH:MM" format (e.g., "14:30")
    end_time: str    # Use "HH:MM" format
    #start_date: datetime
    #end_date: datetime
    #start_time: time
    #end_time: time
    
class WeeklyTasksInCalendar(BaseModel):
    tasks: List[TaskInCalendar]

class GoogleCalendarIntegration:
    SCOPES = ['https://www.googleapis.com/auth/calendar']
    
    def __init__(self):
        self.creds = None
        self.service = None
        self.initialize_credentials()
    
    def initialize_credentials(self):
        """Initialize Google Calendar credentials"""
        # The file token.pickle stores the user's access and refresh tokens
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                self.creds = pickle.load(token)
                
        # If there are no (valid) credentials available, let the user log in
        if not self.creds or not self.creds.valid:
            if (self.creds and self.creds.expired and self.creds.refresh_token):
                self.creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'cal_credentials.json', self.SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            with open('token.pickle', 'wb') as token:
                pickle.dump(self.creds, token)
        
        self.service = build('calendar', 'v3', credentials=self.creds)
    
    def get_busy_times(self, start_date, end_date):
        """Get busy time slots from Google Calendar"""
        calendar_list = self.service.calendarList().list().execute()
        calendar_ids = [calendar['id'] for calendar in calendar_list['items']]
        
        body = {
            "timeMin": start_date.isoformat() + 'Z',
            "timeMax": end_date.isoformat() + 'Z',
            "items": [{"id": cal_id} for cal_id in calendar_ids]
        }
        
        events_result = self.service.freebusy().query(body=body).execute()
        busy_times = []
        
        for calendar_id, calendar_info in events_result['calendars'].items():
            busy_times.extend(calendar_info['busy'])
            
        return busy_times
    
    def create_calendar_events(self, weekly_schedule):
        """Create calendar events for scheduled tasks"""
        for task_in_calendar in weekly_schedule.tasks:
            # Create event using the date and time strings directly
            start_datetime = f"{task_in_calendar.start_date}T{task_in_calendar.start_time}:00"
            end_datetime = f"{task_in_calendar.end_date}T{task_in_calendar.end_time}:00"
            
            event = {
                'summary': f"ToDo: {task_in_calendar.task.task_name}",
                'description': f"Task ID: {task_in_calendar.task.task_ID}\nDifficulty: {task_in_calendar.task.difficulty_level}",
                'start': {
                    'dateTime': start_datetime,
                    'timeZone': 'America/Los_Angeles',  # Pacific Time
                },
                'end': {
                    'dateTime': end_datetime,
                    'timeZone': 'America/Los_Angeles',  # Pacific Time
                },
                'reminders': {
                    'useDefault': True
                }
            }
            
            try:
                self.service.events().insert(calendarId='primary', body=event).execute()
                print(f"Created calendar event for: {task_in_calendar.task.task_name}")
            except Exception as e:
                print(f"Error creating event for {task_in_calendar.task.task_name}: {str(e)}")
                

# Create my Agent
class ToDoAgent:
    def __init__(self):
        self.calendar = GoogleCalendarIntegration()
        
    def predict_tasks_with_llm(self, task_list, api_key=None):
        """
        Generate list of tasks with characteristics using OpenAI's GPT model.
        """
        if api_key:
            openai.api_key = api_key
        else:
            raise ValueError("API key is required")

        # Create a prompt to instruct OpenAI
        prompt = f"""
          Create a structured task list with characteristics for each of these tasks:

          {task_list}

          Each resulting task should be in JSON format following the {TodoTask} format
          For long or difficult tasks, create meaningful smaller subtasks.
          For subtasks, use the version-style task_ID format (e.g., 1.1, 1.2, 1.3 for subtasks of task 1).
          Requirements:
          1. Each task should be a separate main task with a unique task_ID (1, 2, 3, etc.)
          2. For each main task that is complex or difficult:
            - Break it down into subtasks
            - Use decimal notation for subtask IDs (e.g., 1.1, 1.2, 1.3 for subtasks of task 1)
            - Ensure subtasks are meaningful and concrete
          3. Each task and subtask must follow the {TodoTask} format

          Return a JSON array of the tasks in {TodoTaskList} format.
          """

        # Call OpenAI API with more specific example
        response = openai.beta.chat.completions.parse(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "system", "name": "example_user", "content": "organize dinner party, clean garage, write blog post"},
                {"role": "system", "name": "example_assistant", "content": ("1 Organize dinner party with subtasks: 1.1 Create guest list, 1.2 Plan menu, 1.3 Buy groceries, 1.4 Cook, 1.5 Setup table, "
                    "2 Clean garage with subtasks: 2.1 Sort items, 2.2 Organize tools, 2.3 Sweep floor, 2.4 go to recycling center, "
                    "3 Write blog post with no subtasks, "
                    "4 Pay electricity bill with no subtasks")}
            ],
            model="gpt-4o-mini",
            temperature=0.7,
            response_format=TodoTaskList
        )
        
        return response.choices[0].message.content

    def predict_timeslots_with_llm(self, tasks_subtasks, api_key=None):
        """Propose timeslots for tasks during the week using OpenAI's GPT model."""
        if api_key:
            openai.api_key = api_key
        else:
            raise ValueError("API key is required")

        # Calculate date range
        tomorrow = datetime.now().date() + timedelta(days=1)
        date_range = [tomorrow + timedelta(days=i) for i in range(7)]
        date_examples = [d.strftime("%Y-%m-%d") for d in date_range]
        print("DEBUG date examples:", date_examples)
        
        # Convert tasks to JSON
        if isinstance(tasks_subtasks, list) and all(isinstance(t, TodoTask) for t in tasks_subtasks):
            tasks_json = json.dumps([t.dict() for t in tasks_subtasks], indent=2)
        elif isinstance(tasks_subtasks, str):
            tasks_json = tasks_subtasks
        else:
            raise ValueError("tasks_subtasks must be either a list of TodoTask objects or a JSON string")

        # Create prompt with specific date range
        prompt = f"""
        Create a schedule for these tasks over the next 7 days ({date_examples[0]} to {date_examples[-1]}):

        {tasks_json}

        Requirements:
        - Schedule tasks only on these dates: {", ".join(date_examples)}
        - Avoid: Mon-Fri 9am-5pm (work hours)
        - Avoid: 11pm-7am (sleep)
        - Prefer: afternoon/evening slots
        - For outdoor tasks: add 1h before/after for travel
        - Use lunch (12-1pm) except Wednesdays
        - Wednesday: avoid 8-9am and 5-6pm (commute)
        - Balance outdoor/indoor and fun/boring tasks across the week
        - If a task has subtasks, schedule only the subtasks
        - Make sure total duration of subtasks equals main task duration
        - Schedule a task or subtask only once

        Return a JSON array in this exact format:
        {{
          "tasks": [
            {{
              "task": <TodoTask object>,
              "start_date": "YYYY-MM-DD",
              "end_date": "YYYY-MM-DD",
              "start_time": "HH:MM",
              "end_time": "HH:MM"
            }}
          ]
        }}
        """

        try:
            #response = client.beta.chat.completions.create(
            response = openai.beta.chat.completions.parse(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                model="gpt-4o-mini",
                temperature=1,
                response_format=WeeklyTasksInCalendar
            )

            # Get the response content
            response_content = response.choices[0].message.content.strip()
            print("Raw response:", response_content)  # Debug print

            # Clean up the response
            if "```json" in response_content:
                response_content = response_content.split("```json")[1].split("```")[0].strip()
            elif "```" in response_content:
                response_content = response_content.split("```")[1].strip()

            # Parse JSON
            schedule_data = json.loads(response_content)
            
            if not isinstance(schedule_data, dict) or 'tasks' not in schedule_data:
                raise ValueError("Invalid response format: missing 'tasks' key")

            # Convert to calendar tasks
            calendar_tasks = []
            for task_data in schedule_data['tasks']:
                if not isinstance(task_data, dict):
                    print(f"Skipping invalid task data: {task_data}")
                    continue

                try:
                    # Create TodoTask object
                    task = TodoTask(**task_data['task'])
                    
                    # Create TaskInCalendar object
                    calendar_task = TaskInCalendar(
                        task=task,
                        start_date=task_data['start_date'],
                        end_date=task_data['end_date'],
                        start_time=task_data['start_time'],
                        end_time=task_data['end_time']
                    )
                    calendar_tasks.append(calendar_task)
                except Exception as e:
                    print(f"Error processing task: {str(e)}")
                    print(f"Task data: {task_data}")
                    continue

            if not calendar_tasks:
                raise ValueError("No valid tasks could be processed")

            return WeeklyTasksInCalendar(tasks=calendar_tasks)

        except Exception as e:
            print(f"Error in predict_timeslots_with_llm: {str(e)}")
            print(f"Response content: {response_content}")
            return None

    def schedule_tasks_in_calendar(self, weekly_schedule):
        """Schedule the tasks in Google Calendar"""
        if weekly_schedule and isinstance(weekly_schedule, WeeklyTasksInCalendar):
            self.calendar.create_calendar_events(weekly_schedule)

# Initialize the agent
agent = ToDoAgent()

# Extract subtasks from the main tasks list
def extract_subtasks(tasks: List[TodoTask]):
    main_tasks = []
    all_subtasks = []
    
    for task in tasks:
        if task.has_subtasks and task.subtasks:
            # Add the main task to main_tasks
            main_tasks.append(task)
            # Add its subtasks to all_subtasks
            all_subtasks.extend(task.subtasks)
        else:
            main_tasks.append(task)
    
    return main_tasks, all_subtasks

css_js = """
<style>
.task-container {
    font-size: 1em;
    margin-top: 20px;
    font-family: sans-serif;
}
.styled-table {
    border-collapse: separate;
    border-spacing: 0;
    margin: 20px 0;
    font-size: 1em;
    width: 100%;
    box-shadow: 0 0 20px rgba(0, 0, 0, 0.15);
    border-radius: 10px;
    overflow: hidden;
}
.styled-table thead tr {
    background-color: #add8e6;
    color: #ffffff;
    text-align: left;
    font-size: 1.1em;
}
.task-with-subtasks {
    background-color: #f5f5f5 !important;
}
.task-name.clickable {
    cursor: pointer;
    color: #add8e6;
    font-weight: bold;
    position: relative;
    padding-right: 20px;
}
.task-name.clickable:after {
    content: '▼';
    position: absolute;
    right: 0;
    color: #add8e6;
    font-size: 0.8em;
}
.task-name.clickable:hover {
    color: #007559;
    text-decoration: underline;
}
.subtasks-container {
    margin: 10px 0 30px 20px;
}
.subtasks-table {
    display: none;
    border-left: 3px solid #add8e6;
}
/* Style the textbox to match the theme */
#component-0 {
    border: 1px solid #add8e6;
    border-radius: 10px;
    padding: 15px;
    margin: 20px 0;
    font-family: sans-serif;
}
.schedule-table thead tr {
    background-color: #ffcccb !important;  /* Light red */
}
</style>
<script>
function toggleSubtaskTable(taskId) {
    const subtaskTable = document.getElementById('subtasks-' + taskId);
    const taskRow = document.getElementById('task-' + taskId);
    if (subtaskTable) {
        if (subtaskTable.style.display === 'none') {
            subtaskTable.style.display = 'table';
            taskRow.querySelector('.task-name').textContent = '▼';
        } else {
            subtaskTable.style.display = 'none';
            taskRow.querySelector('.task-name').textContent = '▶';
        }
    }
}
</script>
"""

def generate_html_table_with_subtasks(main_tasks: List[TodoTask]) -> str:
    html = """
    <style>
    .styled-table { width: 80%; }
    .subtasks { display: none; margin-left: 20px; }
    .clickable { cursor: pointer; color: #add8e6; }
    .clickable:hover { text-decoration: underline; }
    </style>
    """
    html += "<table class='styled-table'><thead><tr>"
    for field in TodoTask.__fields__.keys():
        html += f"<th>{field}</th>"
    html += "</tr></thead><tbody>"
    
    for task in main_tasks:
        has_subtasks = hasattr(task, 'has_subtasks') and task.subtasks
        html += "<tr>"
        for field in TodoTask.__fields__.keys():
            if field != "subtasks":
                value = getattr(task, field, "")
                if field == "task_name" and has_subtasks:
                    html += f"<td class='clickable' onclick='toggleSubtasks(\"{task.task_ID}\")'>▶ {value}</td>"
                else:
                    html += f"<td>{value}</td>"
        html += "</tr>"
        
        if has_subtasks:
            html += f"<tr id='subtasks-{task.task_ID}' class='subtasks'><td colspan='50%'><table>"
            for subtask in task.subtasks:
                html += "<tr>"
                for field in TodoTask.__fields__.keys():
                    if field != "subtasks":
                        html += f"<td>{getattr(subtask, field, '')}</td>"
                html += "</tr>"
            html += "</table></td></tr>"
    
    html += "</tbody></table>"
    html += """
    <script>
    function toggleSubtasks(taskId) {
        const subtasks = document.getElementById('subtasks-' + taskId);
        if (subtasks.style.display === 'none') {
            subtasks.style.display = 'table-row';
        } else {
            subtasks.style.display = 'none';
        }
    }
    </script>
    """
    return html

# Add at the top of the file after imports
generated_tasks = None
generated_schedule = None
last_main_tasks_html = None
last_subtasks_html = None

def process_todo_list(todo_input):
    """
    Process the todo list input and return tasks with their characteristics
    
    Args:
        todo_input (str): Comma-separated list of tasks
    """
    global generated_tasks, generated_schedule, last_main_tasks_html, last_subtasks_html
    try:
        # Get API key with detailed error checking
        api_key = get_openai_key()
        
        # Set OpenAI API key globally
        openai.api_key = api_key
        
        # Convert input string to list
        todo_list = [task.strip() for task in todo_input.split(',')]
        
        # Get tasks with characteristics
        generated_text = agent.predict_tasks_with_llm(task_list=todo_list, api_key=api_key)
        print("DEBUG Generated Text:", generated_text)

        # Clean up the response
        if generated_text.startswith("```json"):
            generated_text = generated_text[len("```json"):].strip()
        if generated_text.endswith("```"):
            generated_text = generated_text[:-len("```")].strip()
        
        # Parse JSON and ensure it's in the correct format
        print("Raw response:", generated_text)
        tasks_data = json.loads(generated_text)
        print("DEBUG JSON Tasks data:", tasks_data)
        print(f"Number of main tasks: {len(tasks_data['tasks'])}")
        for task in tasks_data['tasks']:
            print(f"Task {task['task_ID']}: {task['task_name']}")
            if task.get('subtasks'):
                print(f"  Subtasks: {len(task['subtasks'])}")

        if isinstance(tasks_data, dict):
            tasks_data = tasks_data.get('tasks', [])
        elif isinstance(tasks_data, str):
            tasks_data = json.loads(tasks_data)
            if isinstance(tasks_data, dict):
                tasks_data = tasks_data.get('tasks', [])
                
        # Debug print
        print("Number of tasks:", len(tasks_data))
        print("Tasks data structure:", json.dumps(tasks_data, indent=2))
        
        # Convert JSON data to TodoTask objects, handling both main tasks and subtasks
        main_tasks_list = []
        for task_data in tasks_data:
            if isinstance(task_data, dict):
                # Convert subtasks if they exist
                if task_data.get('subtasks'):
                    task_data['subtasks'] = [TodoTask(**subtask) for subtask in task_data['subtasks']]
                main_tasks_list.append(TodoTask(**task_data))
        
        # Debug print
        print("Number of main tasks:", len(main_tasks_list))
        
        # Store the generated tasks globally
        generated_tasks = main_tasks_list
        
        # ...rest of the existing code...
        
        # Combine all HTML with debug information
        main_tasks, subtasks = extract_subtasks(main_tasks_list)
        html_result_main = generate_html_table_with_subtasks(main_tasks_list)  # Use full main_tasks_list
        html_result_sub = generate_html_table_with_subtasks(subtasks) if subtasks else ""
        
        # Store the HTML for later use
        last_main_tasks_html = html_result_main
        last_subtasks_html = html_result_sub if subtasks else ""

        # After generating tasks, immediately create schedule
        tasks_to_schedule = []
        for task in main_tasks_list:
            if task.has_subtasks and task.subtasks:
                tasks_to_schedule.extend(task.subtasks)
            else:
                tasks_to_schedule.append(task)
        
        # Get schedule from OpenAI
        generated_schedule = agent.predict_timeslots_with_llm(tasks_to_schedule, api_key=api_key)
        
        # Generate schedule HTML if available
        schedule_html = ""
        if generated_schedule:
            schedule_html = """
            <h3>Preliminary Schedule:</h3>
            <table class='styled-table schedule-table'>
            <thead class="schedule-header">
                <tr>
                    <th>Task ID</th>
                    <th>Task Name</th>
                    <th>Date</th>
                    <th>Start Time</th>
                    <th>Duration (min)</th>
                    <th>Difficulty</th>
                </tr>
            </thead>
            <tbody>
            """
            
            # Sort tasks by date and time
            sorted_tasks = sorted(
                generated_schedule.tasks,
                key=lambda x: (x.start_date, x.start_time)
            )
            
            for task_in_calendar in sorted_tasks:
                task = task_in_calendar.task
                task_id = task.task_ID
                is_subtask = '.' in task_id
                
                schedule_html += f"""
                <tr class="{'subtask' if is_subtask else 'main-task'}">
                    <td>{task_id}</td>
                    <td>{task.task_name}</td>
                    <td>{task_in_calendar.start_date}</td>
                    <td>{task_in_calendar.start_time}</td>
                    <td>{task.estimated_duration}</td>
                    <td>{task.difficulty_level}</td>
                </tr>
                """
            schedule_html += "</tbody></table>"

        # Combine all HTML sections
        result_html = f"""
        {css_js}
        <div class="task-container">
            <h3>Main Tasks ({len(main_tasks_list)}):</h3>
            {html_result_main}
            {f'<h3>Sub Tasks ({len(subtasks)}):</h3>{html_result_sub}' if subtasks else ''}
            {schedule_html}
        </div>
        """
        return result_html
        
    except Exception as e:
        import traceback
        return f"Error processing todo list: {str(e)}\n{traceback.format_exc()}"

def process_my_schedule(todo_input):
    """Process the todo list to generate a weekly schedule"""
    global generated_tasks, generated_schedule, last_main_tasks_html, last_subtasks_html
    try:
        if not generated_tasks:
            return "Please generate tasks first by clicking 'Generate Tasks' button"

        api_key = get_openai_key()
        openai.api_key = api_key

        # Always generate a new schedule when this function is called
        tasks_to_schedule = []
        for task in generated_tasks:
            if task.has_subtasks and task.subtasks:
                tasks_to_schedule.extend(task.subtasks)
            else:
                tasks_to_schedule.append(task)

        # Get new schedule from OpenAI
        generated_schedule = agent.predict_timeslots_with_llm(tasks_to_schedule, api_key=api_key)
        
        if not generated_schedule:
            return "Failed to generate schedule. Please try again."

        # Generate HTML table for schedule
        schedule_html = """
        <h3>Weekly Schedule:</h3>
        <table class='styled-table schedule-table'>
        <thead class="schedule-header">
            <tr>
                <th>Task ID</th>
                <th>Task Name</th>
                <th>Date</th>
                <th>Start Time</th>
                <th>Duration (min)</th>
                <th>Difficulty</th>
            </tr>
        </thead>
        <tbody>
        """
        
        # Sort tasks by date and time
        sorted_tasks = sorted(
            generated_schedule.tasks,
            key=lambda x: (x.start_date, x.start_time)
        )
        
        for task_in_calendar in sorted_tasks:
            task = task_in_calendar.task
            task_id = task.task_ID
            is_subtask = '.' in task_id
            
            schedule_html += f"""
            <tr class="{'subtask' if is_subtask else 'main-task'}">
                <td>{task_id}</td>
                <td>{task.task_name}</td>
                <td>{task_in_calendar.start_date}</td>
                <td>{task_in_calendar.start_time}</td>
                <td>{task.estimated_duration}</td>
                <td>{task.difficulty_level}</td>
            </tr>
            """
        
        schedule_html += "</tbody></table>"
        
        # Combine all sections
        result_html = f"""
        {css_js}
        <div class="task-container">
            <h3>Main Tasks:</h3>
            {last_main_tasks_html}
            {f'<h3>Sub Tasks:</h3>{last_subtasks_html}' if last_subtasks_html else ''}
            {schedule_html}
        </div>
        """
        return result_html
        
    except Exception as e:
        import traceback
        return f"Error processing schedule: {str(e)}\n{traceback.format_exc()}"

# Add new function to handle calendar booking
def book_my_calendar(todo_input):
    """Book the scheduled tasks in Google Calendar"""
    global generated_tasks, generated_schedule
    try:
        if not generated_tasks:
            return "Please generate tasks first by clicking 'Generate Tasks' button"
        
        if not generated_schedule:
            return "Please generate schedule first by clicking 'Generate My Schedule' button"

        api_key = get_openai_key()
        openai.api_key = api_key

        # Use the agent to schedule tasks in calendar
        agent.schedule_tasks_in_calendar(generated_schedule)
        
        return """
        <div style='padding: 20px; background-color: #e8f5e9; border-radius: 10px; margin: 20px 0;'>
            <h3 style='color: #2e7d32; margin-top: 0;'>✅ Tasks Successfully Booked!</h3>
            <p>Your tasks have been added to your Google Calendar.</p>
            <p>Check your calendar to see the scheduled events.</p>
        </div>
        """
    except Exception as e:
        import traceback
        return f"Error booking calendar: {str(e)}\n{traceback.format_exc()}"

# Create Gradio interface using Blocks
with gr.Blocks(theme=gr.themes.Soft()) as iface:
    gr.Markdown("<div style=\"text-align: center;font-size: 24px; font-weight: bold;\">AI ToDo Assistant</div>")
    gr.Markdown("**Welcome to your AI ToDo assistant.** I will help you schedule the tasks you would like to accomplish this week.") 
    gr.Markdown("Enter your tasks and get them organized with estimated durations, difficulty levels, and other characteristics.")
    
    with gr.Row():
        with gr.Column(scale=4):
            text_input = gr.Textbox(
                lines=3,
                placeholder="Enter your tasks, separated by commas (e.g., bike ride, pay bills, clean house). Click 1. Generate Tasks",
                label="My To-Do List for this week"
            )
        with gr.Column(scale=1):
            with gr.Row():
                generate_btn = gr.Button("1. Generate Tasks", min_width="100px")
            with gr.Row():
                schedule_btn = gr.Button("2. Generate My Schedule", min_width="100px")
            with gr.Row():
                calendar_btn = gr.Button("3. Book My Calendar", min_width="100px")
    
    output_html = gr.HTML()
    
    # Connect button click events to their respective functions
    generate_btn.click(
        fn=process_todo_list,
        inputs=text_input,
        outputs=output_html
    )
    
    schedule_btn.click(
        fn=process_my_schedule,
        inputs=text_input,
        outputs=output_html
    )
    
    calendar_btn.click(
        fn=book_my_calendar,
        inputs=text_input,
        outputs=output_html
    )

if __name__ == "__main__":
    iface.launch(share=False)


