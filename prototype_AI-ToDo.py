# List of tasks
tasks = [
    'bike ride',
    'pay electricity bill',
    'decorate house for christmas',
    'clean the bathroom',
    'call my friend Linda'
]

# Print all tasks
print("My To-Do List:")
print("-" * 20)
for index, task in enumerate(tasks, 1):
    print(f"{index}. {task}") 