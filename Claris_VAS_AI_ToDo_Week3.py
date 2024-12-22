# Install necessary packages , just ONE TIME, comment after.

#%pip install --upgrade openai=1.35.14
#%pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client#%pip install --upgrade openai httpx==0.23.0

# Install necessary packages with compatible versions
#%pip install openai==1.3.0 httpx==0.24.1 httpcore==0.18.0

# Import Libraries
import random, openai, json, os
import pandas as pd
#from google.colab import userdata
from pydantic import BaseModel
from typing import List
from datetime import time
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import os.path
from datetime import datetime, timedelta

# If you would like to use openai,
# please define the openai_key below otherwise leave as None
#openai_key = userdata.get('openaikey')

# Get API key from environment variable
openai_key = os.environ.get('MYOPENAIKEY')
if not openai_key:
    print("Warning: MYOPENAIKEY environment variable not found")

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
class TaskSchedule(BaseModel):
    task_id: str
    task_name: str
    day: str
    start_time: time
    duration_minutes: int
    difficulty_level: str

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
            client = openai.OpenAI(api_key=api_key)
        else:
            raise ValueError("API key is required")

        # Create a prompt to instruct OpenAI
        prompt = f"""
          Get a list of tasks with their characteristics based on the following list of strings

          {task_list}

          Each resulting task should be in JSON format with these fields:
                      - "task_ID": unique integer for main tasks, for subtasks use version style (e.g., if parent task is 1, subtasks should be 1.1, 1.2, 1.3, etc.)
                      - "task_name": text
          - "estimated_duration": a number in minutes
          - "category": a text defining a general category
          - "difficulty_level": a value within ("easy", "medium", "difficult")
          - "ind_outside": a boolean indicating if the task is done inside or outside
          - "ind_travel": a boolean indicating if the task requires travelling
          - "status": a value within ("not started", "done", "partially done", "reschedule"), initially this value is not started
          - "actual_duration": a number in minutes, initially this value is 0
          - "estimated_remaining_duration": a number in minutes or hours, initially this value is 0

                      For long or difficult tasks, create meaningful smaller subtasks.
                      For subtasks, use the version-style task_ID format (e.g., 1.1, 1.2, 1.3 for subtasks of task 1).

          Return only a JSON array of the tasks.
          """

        # Call OpenAI API to generate questions
        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="gpt-4o-mini",
            #model="gpt-3.5-turbo" OLD model,
            temperature=1
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
            client = openai.OpenAI(api_key=api_key)
        else:
            raise ValueError("API key is required")

        # Create a prompt to instruct OpenAI
        prompt = f"""
          Propose some timeslots in my week to accomplish the following tasks:

          {tasks_subtasks}

          The week starts on Monday.
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

          Return the schedule as a JSON array with the following structure for each task:
          {{
              "task_id": "string (e.g., '1' or '1.1')",
              "task_name": "string",
              "day": "string (Monday/Tuesday/etc.)",
              "start_time": "HH:MM (24-hour format)",
              "duration_minutes": integer,
              "difficulty_level": "string (easy/medium/difficult)"
          }}

          Return only the JSON array, no additional text.
        """

        response = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="gpt-4o-mini",
            #model="gpt-3.5-turbo", OLD model
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
            # Convert time strings to time objects
            for task in schedule_data:
                task["start_time"] = time.fromisoformat(task["start_time"])
            
            # Validate and create structured schedule
            weekly_schedule = WeeklySchedule(tasks=schedule_data)
            return weekly_schedule
        except Exception as e:
            print(f"Error parsing schedule: {str(e)}")
            print("Raw response:", response_content)  # Add this line for debugging
            return None

    def schedule_tasks_in_calendar(self, weekly_schedule):
        """Schedule the tasks in Google Calendar"""
        if weekly_schedule:
            self.calendar.create_calendar_events(weekly_schedule)

# Get Output with my Agent
agent = ToDoAgent()


# Provide my todo list
todo_list = ['bike ride', 'pay the bills', 'decorate house for christmas', 'clean my home', 'host dinner with Jack&Jill']

# Get tasks predicted with duration and characteristics
generated_text = agent.predict_tasks_with_llm(task_list=todo_list, api_key=openai_key)

# parse output. This is response from chatgpt. JSON format
#if openai_key:
#  print(generated_text)

# parse output. This is the response from chatgpt in JSON format
if openai_key:
  # remove header for json result and any leading or trailing whitespace and
  # backticks from the response
  if generated_text.startswith("```json"):
    generated_text = generated_text[len("```json"):]
  if generated_text.endswith("```"):
    generated_text = generated_text[:-len("```")]
  json_tasks = generated_text.strip().strip("")

  # Convert the JSON string to a Python list of dictionaries
  tasks = json.loads(json_tasks)

  # Print as a table, use pandas dataframe
  # Create a pandas DataFrame from the list of dictionaries
  df_main_tasks = pd.DataFrame(tasks)

  # Display the DataFrame as a table
  print("\nMain Tasks:")
  print(df_main_tasks.to_string()) # Replace display with print

  # If there are subtasks , print them in a different table
  subtasks = []
  for task in tasks:
    if 'subtasks' in task and task['subtasks']: # Check if subtasks exist for the task
      for subtask in task['subtasks']:
        subtask['parent_task_ID'] = task['task_ID'] # Add parent task ID to subtask
        subtasks.append(subtask)

  if subtasks:
    df_subtasks = pd.DataFrame(subtasks)
    print("\nSubtasks:")
    print(df_subtasks.to_string()) # Replace display with print
else:
  print(generated_text)

# Get tasks predicted with duration and characteristics
generated_schedule = agent.predict_timeslots_with_llm(tasks_subtasks=json_tasks, api_key=openai_key)

if openai_key and generated_schedule:
    print("\nWeekly Schedule:")
    for task in generated_schedule.tasks:
        print(f"{task.day} at {task.start_time.strftime('%H:%M')} - "
              f"Task {task.task_id}: {task.task_name} "
              f"(Duration: {task.duration_minutes} mins, "
              f"Difficulty: {task.difficulty_level})")
    
    # Add tasks to Google Calendar
    print("\nAdding tasks to Google Calendar...")
    agent.schedule_tasks_in_calendar(generated_schedule)