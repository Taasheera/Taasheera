"""Paying for an application's submission (CMPS271-106, CMPS271-108, CMPS271-109).

Everything up to the print-ready packet is free. Payment unlocks submission: the form-filling agent
filling the embassy's online form. The only provider for now is simulated: the traveler sees the
price breakdown and presses Pay, and the payment is recorded as paid without charging anything.
No card details are asked for, sent or stored. Behind the `payments` flag.
"""
