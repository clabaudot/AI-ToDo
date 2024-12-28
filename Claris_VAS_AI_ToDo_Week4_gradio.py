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

class TodoTaskList(BaseModel):
    tasks: list[TodoTask]

class TaskInCalendar(BaseModel):
    task: TodoTask
    start_date: datetime
    end_date: datetime
    start_time: time
    end_time: time
    
class WeeklyTasksInCalendar(BaseModel):
    tasks: list[TaskInCalendar]

# SHould be deleted
class TaskSchedule(BaseModel):
    task_id: str
    task_name: str
    day: str
    start_time: time
    duration_minutes: int
    difficulty_level: str

# SHould be deleted
class WeeklySchedule(BaseModel):
    tasks: List[TaskSchedule]

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
            if self.creds and self.creds.expired and self.creds.refresh_token:
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
        # Get the current date for the start of the week (Monday)
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        
        for task in weekly_schedule.tasks:
            # Convert task time to datetime
            task_time = datetime.strptime(f"{task.day} {task.start_time.strftime('%H:%M')}", 
                                        "%A %H:%M")
            
            # Adjust the date to the correct day of the current week
            days_ahead = task_time.weekday()
            task_date = monday + timedelta(days=days_ahead)
            
            start_time = datetime.combine(task_date.date(), task.start_time)
            end_time = start_time + timedelta(minutes=task.duration_minutes)
            
            event = {
                'summary': f"ToDo: {task.task_name}",
                'description': f"Task ID: {task.task_id}\nDifficulty: {task.difficulty_level}",
                'start': {
                    'dateTime': start_time.isoformat(),
                    'timeZone': 'America/Los_Angeles',  # Pacific Time
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': 'America/Los_Angeles',  # Pacific Time
                },
                'reminders': {
                    'useDefault': True
                }
            }
            
            try:
                self.service.events().insert(calendarId='primary', body=event).execute()
                print(f"Created calendar event for: {task.task_name}")
            except Exception as e:
                print(f"Error creating event for {task.task_name}: {str(e)}")
                

# Create my Agent
class ToDoAgent:
    def __init__(self):
        self.calendar = GoogleCalendarIntegration()
        
    def predict_tasks_with_llm(self, task_list, api_key=None):
        """
        Generate list of tasks with characteristices using OpenAI's GPT model.
        """
        # Set OpenAI API key if provided
        if api_key:
            openai.api_key = api_key
        else:
            raise ValueError("API key is required")

        # Create a prompt to instruct OpenAI
        prompt = f"""
          Get a list of tasks with their characteristics based on the following list of strings

          {task_list}

          Each resulting task should be in JSON format following the {TodoTask} format
          For long or difficult tasks, create meaningful smaller subtasks.
          For subtasks, use the version-style task_ID format (e.g., 1.1, 1.2, 1.3 for subtasks of task 1).

          Return an array of the tasks in {TodoTaskList} format.
          """
          #Return only a JSON array of the tasks in {TodoTaskList} format.

        # Call OpenAI API to generate questions
        response = openai.beta.chat.completions.parse(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "system", "name": "example_user",      "content": "Dinner with friends"},
                {"role": "system", "name": "example_assistant", "content": "1.1 call them to confirm the day in the week, 1.2 plan the menu, 1.3 grocery shopping (outdoor), 1.4 cook, 1.5 set the table"},
                {"role": "system", "name": "example_user",      "content": "Decorate house for Christmas"},
                {"role": "system", "name": "example_assistant", "content": "2.1 get the decorations from attic, 2.2 buy a tree (outdoor), 2.3 set up the tree, 2.4 decorate the tree, 2.5 set up the indoor lights, 2.6 set up the outdoor lights"},
                {"role": "system", "name": "example_user",      "content": "Plan a trip to Italy"},
                {"role": "system", "name": "example_assistant", "content": "3.1 Decide on the dates, 3.2 check the flights, 3.3 book the flights, 3.4 define an itinerary, 3.5 plan trasnportation, 3.6 book hotels, 3.7 plan visits"},
            ],
            model="gpt-4o-mini",
            temperature=1,
            response_format=TodoTaskList
        )
        
        # Parse and return the JSON response
        return response.choices[0].message.content

    def predict_timeslots_with_llm(self, tasks_subtasks, api_key=None):
        """
        Propose timeslots for my tasks during the week using OpenAI's GPT model.
        Returns a structured schedule using Pydantic models.
        """
        # Set OpenAI API key if provided
        if api_key:
            openai.api_key = api_key
            client = openai.Client()
        else:
            raise ValueError("API key is required")

        # Create a prompt to instruct OpenAI
        prompt = f"""
          Propose some timeslots in my week to accomplish the following tasks:

          {tasks_subtasks}

          The week starts on Monday.
          Provide the date and time when the task to start.
          Avoid working time which is Monday to Friday from 9:00 am to 5:00 pm. 
          Lunch time 12:00 pm to 1:00 pm can be used except Wednesdays.
          Wednesday I work at the office so avoid the period of 1h commute before and after work.
          Avoid sleeping time from 11pm to 7am.
          Not a morning person so afternoon and evening are better choices.
          If the task is outdoor, plan for 1h to get at the location.
          Balance outdoor and indoor tasks over the week.
          Balance fun tasks and boring tasks over the week.
          Propose timeslots for subtasks and not the main task when there are subtasks.
          Make sure that the duration of the input tasks is respected.
          Make sure the subtasks total durationis the duration of the task.

          Return the schedule as a JSON array following the WeeklyTasksInCalendar format where each task contains:
          - task: TodoTask object with all task details
          - start_date: YYYY-MM-DD format
          - end_date: YYYY-MM-DD format
          - start_time: HH:MM format (24-hour)
          - end_time: HH:MM format (24-hour)

          Return an array of the tasks in {WeeklyTasksInCalendar} format.
          
        """
        #Return only the JSON array, no additional text.

        response = client.beta.chat.completions.parse(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="gpt-4o-mini",
            response_format=WeeklyTasksInCalendar,
            temperature=0.1
        )

        # Get the response content
        response_content = response.choices[0].message.content

        # Clean up the response if it contains markdown code blocks
        if "```json" in response_content:
            response_content = response_content.split("```json")[1].split("```")[0]
        elif "```" in response_content:
            response_content = response_content.split("```")[1]
        
        # Remove any leading/trailing whitespace
        response_content = response_content.strip()

        try:
            schedule_data = json.loads(response_content)
            # Convert the schedule data to WeeklyTasksInCalendar format
            calendar_tasks = []
            for task_data in schedule_data:
                task = TodoTask(**task_data['task'])
                calendar_task = TaskInCalendar(
                    task=task,
                    start_date=datetime.strptime(task_data['start_date'], '%Y-%m-%d'),
                    end_date=datetime.strptime(task_data['end_date'], '%Y-%m-%d'),
                    start_time=datetime.strptime(task_data['start_time'], '%H:%M').time(),
                    end_time=datetime.strptime(task_data['end_time'], '%H:%M').time()
                )
                calendar_tasks.append(calendar_task)
            
            weekly_schedule = WeeklyTasksInCalendar(tasks=calendar_tasks)
            return weekly_schedule
        except Exception as e:
            print(f"Error parsing schedule: {str(e)}")
            print("Raw response:", response_content)  # Add this line for debugging
            return None


    def schedule_tasks_in_calendar(self, weekly_schedule):
        """Schedule the tasks in Google Calendar"""
        if weekly_schedule and isinstance(weekly_schedule, WeeklyTasksInCalendar):
            self.calendar.create_calendar_events(weekly_schedule)

# Initialize the agent
agent = ToDoAgent()

# Should be deleted
# Function to convert TodoTask list to HTML table
def tasks_to_html_table(tasks):
    if not tasks:
        return "<p>No tasks available.</p>"
    
    html = "<table border='1'>"
    # Add table headers
    html += "<tr>"
    for field in TodoTask.__fields__.keys():
        html += f"<th>{field}</th>"
    html += "</tr>"
    # Add table rows
    for task in tasks:
        html += "<tr>"
        for field in TodoTask.__fields__.keys():
            html += f"<td>{getattr(task, field)}</td>"
        html += "</tr>"
    html += "</table>"
    return html


# Should be deleted
# Function to create an HTML table from the tasks
def generate_html_table(tasks: List[TodoTask]) -> str:
    html = "<table border='1' style='border-collapse: collapse; width: 100%;'>"
    html += "<tr>" + "".join(f"<th>{field}</th>" for field in TodoTask.__fields__.keys()) + "</tr>"
    for task in tasks:
        html += "<tr>" + "".join(f"<td>{getattr(task, field)}</td>" for field in TodoTask.__fields__.keys()) + "</tr>"
    html += "</table>"
    return html

# Extract subtasks from the main tasks list
def extract_subtasks(tasks: List[TodoTask]):
    main_tasks = []
    all_subtasks = []
    
    for task in tasks:
        if task.has_subtasks:  # If the task has subtasks
            subtasks = task.subtasks if hasattr(task, 'subtasks') else []
            all_subtasks.extend(subtasks)
        else:
            main_tasks.append(task)
    
    return main_tasks, all_subtasks

# Function to create an HTML table from the tasks and subtasks
def generate_html_table_with_subtasks(main_tasks: List[TodoTask], subtasks: List[TodoTask]) -> str:
    html = "<table class='styled-table'>"
    # Table headers
    html += "<thead><tr>" + "".join(f"<th>{field}</th>" for field in TodoTask.__fields__.keys()) + "</tr></thead><tbody>"
    
    for task in main_tasks:
        html += "<tr>"
        for field in TodoTask.__fields__.keys():
            html += f"<td>{getattr(task, field)}</td>"
        html += "</tr>"
        
        # Add subtasks as a nested table
        task_subtasks = [sub for sub in subtasks if sub.parent_task_ID == task.task_ID]
        if task_subtasks:
            html += f"<tr><td colspan='{len(TodoTask.__fields__.keys())}'>"
            html += "<table class='subtasks styled-table'>"
            html += "<thead><tr>" + "".join(f"<th>{field}</th>" for field in TodoTask.__fields__.keys()) + "</tr></thead><tbody>"
            for subtask in task_subtasks:
                html += "<tr>" + "".join(f"<td>{getattr(subtask, field)}</td>" for field in TodoTask.__fields__.keys()) + "</tr>"
            html += "</tbody></table></td></tr>"
    
    html += "</tbody></table>"
    return html

def process_todo_list(todo_input):
    """
    Process the todo list input and return tasks with their characteristics
    
    Args:
        todo_input (str): Comma-separated list of tasks
    """
    try:
        # Get API key with detailed error checking
        api_key = get_openai_key()
        
        # Set OpenAI API key globally
        openai.api_key = api_key
        
        # Convert input string to list
        todo_list = [task.strip() for task in todo_input.split(',')]
        
        # Get tasks with characteristics
        generated_text = agent.predict_tasks_with_llm(task_list=todo_list, api_key=api_key)
        
        # Clean up the response
        if generated_text.startswith("```json"):
            generated_text = generated_text[len("```json"):].strip()
        if generated_text.endswith("```"):
            generated_text = generated_text[:-len("```")].strip()
        
        # Parse JSON
        tasks_data = json.loads(generated_text)
        
        # Ensure tasks_data is a list of dictionaries
        if isinstance(tasks_data, dict):
            tasks_data = tasks_data.get('tasks', [])
        
        # Convert JSON data to TodoTask objects
        main_tasks_list = [TodoTask(**task) for task in tasks_data if isinstance(task, dict)]
        
        # Add CSS for table styling and JavaScript for toggling subtasks
        css_js = """
        <style>
        .styled-table {
            border-collapse: collapse;
            margin: 25px 0;
            font-size: 0.9em;
            font-family: sans-serif;
            min-width: 400px;
            box-shadow: 0 0 20px rgba(0, 0, 0, 0.15);
        }
        .styled-table thead tr {
            background-color: #009879;
            color: #ffffff;
            text-align: left;
        }
        .styled-table th,
        .styled-table td {
            padding: 12px 15px;
        }
        .styled-table tbody tr {
            border-bottom: 1px solid #dddddd;
        }
        .styled-table tbody tr:nth-of-type(even) {
            background-color: #f3f3f3;
        }
        .styled-table tbody tr:last-of-type {
            border-bottom: 2px solid #009879;
        }
        .subtasks {
            display: none;
        }
        </style>
        <script>
        function toggleSubtasks(taskID) {
            var subtasks = document.getElementById('subtasks-' + taskID);
            if (subtasks.style.display === 'none') {
                subtasks.style.display = 'table-row-group';
            } else {
                subtasks.style.display = 'none';
            }
        }
        </script>
        """
        
        # Combine all HTML
        main_tasks, subtasks = extract_subtasks(main_tasks_list)
        html_result = generate_html_table_with_subtasks(main_tasks, subtasks)
        return html_result
        
    except Exception as e:
        import traceback
        return f"Error processing todo list: {str(e)}\n{traceback.format_exc()}"

# Create Gradio interface
iface = gr.Interface(
    fn=process_todo_list,
    inputs=[
        gr.Textbox(
            lines=3,
            placeholder="Enter your tasks, separated by commas (e.g., bike ride, pay bills, clean house)",
            label="Todo List"
        )
    ],
    outputs=gr.HTML(),
    title="AI ToDo Assistant",
    description="Welcome to your AI ToDo assistant. I will help you schedule the tasks you would like to accomplish this week.\nEnter your tasks and get them organized with estimated durations, difficulty levels, and other characteristics.",
    examples=[["bike ride, pay the bills, decorate house for christmas, clean my home, host dinner with Jack&Jill"]],
    theme=gr.themes.Soft()
)

if __name__ == "__main__":
    iface.launch(share=False)