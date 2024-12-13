# AI-ToDo
My project is to create an AI assistant to manage a todo list.
This is a project done in 6 weeks to practice AI capabilities

## Quick Start

## Setup
```pip install -r requirements.txt```

## Run
```python main.py -d data/ -o results/```

# Overview of the AI ToDo
This project is to suggest a time in the week to execute a task from a todo list for the week. It doesn't contain any obvious activities like sleeping, eating, working, dressing, washing, commuting to work. It can contain additional tasks that the user would like to accomplish during the week.  
The tasks can be simple, or complex, in case of a complex task the model will split it in subtasks. The model will recognize the input tasks from a list on generic tasks (the dataset).  
Step 1 : from a todo list generate the list of tasks with their characteristics.  
Step 2 : propose a time in the week to execute the task, during free time, linked to my calendar so there is no overlap with appointments. The model will also respect some of my criteria (not a morning person, working hours, ...).  
Step 3 : update the tasks list daily to mark the progress (done, partially done, reschedule next week, ...).  
Step 4 : send regular notifications during the week measuring the progress and motivating to finish.  

## 1. Input Data Used
The input data is a list of text representing a todo list for the week.  
example: ('bike ride', 'pay electricity bill', 'decorate house for christmas', 'clean the bathroom', 'call my friend Linda') 

## 2. Output Data
The output data is a JSON file containing the same list of tasks (and maybe subtasks) with an estimated duration, a type, a difficulty level, an indicator if it's inside or outside, an indicator if it requires travelling, a suggested time in the week to do it.  

## 3. Models Used
OpenAI GPT-4o  
OpenAI GPT-4o-mini  

## 4. Evaluation Method
Not sure yet
