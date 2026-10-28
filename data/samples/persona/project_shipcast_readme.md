# ShipCast: predicting parcel delivery delays

ShipCast predicts whether a parcel will arrive late, so a dispatcher can warn the customer early.
I built it during the Turing College programme using anonymised data shaped like the data I
worked with in logistics.

## Problem
In my old job, about 12% of shipments arrived late, and customers usually found out before we
did. If we could predict delays at dispatch time, we could warn customers and re-route parcels.

## Data
- 180,000 synthetic shipment records: route, carrier, weekday, weight, weather, warehouse load.
- Cleaned and joined with **pandas** and **SQL** (PostgreSQL).

## Model
- Baseline: logistic regression (F1 = 0.61).
- Final: **XGBoost** classifier with engineered features such as "warehouse load in the last
  24 hours" (F1 = 0.74, ROC AUC = 0.86).
- Explained predictions with SHAP so dispatchers can see *why* a parcel is flagged.

## Serving
- A **FastAPI** endpoint `/predict` returns the delay probability and the top 3 reasons.
- Packaged with **Docker**; tests with pytest; CI with GitHub Actions.
- Deployed once to an AWS EC2 instance for the demo.

## Limitations
- Trained on synthetic data, so the real-world accuracy is unknown.
- No automated retraining pipeline yet (I would use Airflow or Prefect next).

## Tech stack
Python, pandas, scikit-learn, XGBoost, SHAP, SQL/PostgreSQL, FastAPI, Docker, GitHub Actions, AWS EC2.
