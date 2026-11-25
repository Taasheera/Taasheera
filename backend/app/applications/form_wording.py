"""How choice answers are written on official forms, shared by the PDF filler and the form agent.

The questionnaire's option labels speak to the traveler ("I'm employed"); an embassy form expects the
noun a consular officer reads ("Employee"). Both outputs use this one table so they never disagree.
"""

OCCUPATION_ON_FORMS = {"employed": "Employee", "self_employed": "Self-employed", "student": "Student",
                       "retired": "Retired", "unemployed": "Unemployed", "homemaker": "Homemaker", "other": "Other"}


def occupation_on_form(answers: dict) -> str | None:
    """The traveler's job title when they gave one, otherwise the form wording of their occupation."""
    title = (answers.get("job_title") or "").strip()
    if title:
        return title
    occupation = answers.get("occupation")
    return OCCUPATION_ON_FORMS.get(occupation) if occupation else None

# Boxes ticked by a field's "fixed" value, as the review table names them.
FIXED_ON_FORMS = {"ordinary": "Ordinary passport", "regular": "Regular", "tourist_l": "L (tourism)"}
SEX_ON_FORMS = {"F": "Female", "M": "Male", "X": "Unspecified (X)"}
