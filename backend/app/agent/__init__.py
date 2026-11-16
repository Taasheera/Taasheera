"""The form-filling agent (CMPS271-236, CMPS271-67, CMPS271-70).

The API queues a run when the traveler asks; a separate worker process (agent_worker.py) fills the
embassy's online form in a headless browser from the traveler's confirmed answers, records every
field it types with a screenshot of each page, and stops before submitting. It never submits.
"""
