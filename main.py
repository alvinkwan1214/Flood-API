from flask import Flask, render_template, request, jsonify
import requests
import pandas as pd
import plotly.express as px
from dash import Dash, dcc, html, Input, Output
from dash.dash_table import DataTable
import os
from datetime import datetime, timedelta
import pytz


PORT = int(os.environ.get("FLASK_RUN_PORT", 8000))

server = Flask(__name__)

@server.route("/")
def home():
    """Home page that displays a table of monitoring stations with warnings."""
    url = "https://environment.data.gov.uk/flood-monitoring/id/stations"
    warnings_url = "https://environment.data.gov.uk/flood-monitoring/id/floods"

    # Fetch station and flood warning data
    response = requests.get(url)
    warnings_response = requests.get(warnings_url)

    stations = []
    
    if response.status_code == 200 and warnings_response.status_code == 200:
        data = response.json()
        warning_data = warnings_response.json()

        warning_dict = {
            str(warning.get("eaAreaName", "Unknown Area")): warning.get("message", "No details available")
            for warning in warning_data.get("items", [])
            if isinstance(warning.get("eaAreaName"), str) 
        }

        stations = []
        for station in data.get("items", []):
            station_id = station.get("notation", "Unknown ID")
            station_name = station.get("label", "Unknown Station")

            if not isinstance(station_name, str):
                station_name = str(station_name)

            # Find if station name appears in any flood warning area
            matching_warnings = [
                warning_dict[area] for area in warning_dict if station_name.lower() in area.lower()
            ]

            stations.append({
                "id": station_id,
                "name": station_name,
                "warnings": matching_warnings if matching_warnings else ["No active flood warnings"]
            })

    return render_template("home.html", stations=stations)


@server.route("/API/")
def API_info():
    return render_template("API.html")

@server.route("/stations")
def get_stations():
    url = "https://environment.data.gov.uk/flood-monitoring/id/stations"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        stations = [
            {"id": station["notation"], "name": station.get("label", "Unknown Station")}
            for station in data.get("items", [])
        ]
        return jsonify(stations)
    return jsonify({"error": "Failed to fetch station data"}), 500

@server.route("/readings/<station_id>")
def get_readings(station_id):
    url = f"https://environment.data.gov.uk/flood-monitoring/id/stations/{station_id}/readings?_sorted&_limit=100"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        readings = [
            {"time": r["dateTime"], "value": r["value"]}
            for r in data.get("items", [])
        ]
        return jsonify(readings)
    return jsonify({"error": "Failed to fetch readings"}), 500

@server.route("/dashboard/")
def dashboard():
    return render_template("dashboard.html")


app = Dash(
    __name__,
    server=server,
    routes_pathname_prefix="/dash/",
    suppress_callback_exceptions=True 
)


# Dash Layout
app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    html.H1("UK Flood Monitoring Dashboard", style={"font-family": "Arial"}),
    
    dcc.Dropdown(
        id="station-dropdown",
        placeholder="Select a station",
        style={"font-family": "Arial"}
    ),
    
    html.Div(
        id="flood-warning",
        style={"color": "red", "fontSize": "18px", "font-family": "Arial"}
    ),

    dcc.Graph(id="readings-graph", style={"font-family": "Arial"}),
    
    html.Div(id="readings-table", style={"font-family": "Arial"}),
], style={"font-family": "Arial"})


@app.callback(
    Output("station-dropdown", "options"),
    Input("station-dropdown", "id"),
    prevent_initial_call=False  # Ensures it runs when the page loads
)
def update_dropdown(_):
    response = requests.get(f"{request.host_url}stations")
    if response.status_code == 200:
        stations = response.json()
        return [{"label": s["name"], "value": s["id"]} for s in stations]
    return []


@app.callback(
    [Output("readings-graph", "figure"), 
     Output("readings-table", "children"), 
     Output("flood-warning", "children")],
    Input("station-dropdown", "value")
)
def update_graph(station_id):
    if not station_id:
        return px.line(title="Select a station to view readings"), "", ""

    readings_response = requests.get(f"http://127.0.0.1:{PORT}/readings/{station_id}")
    warnings_response = requests.get("https://environment.data.gov.uk/flood-monitoring/id/floods")
    measurements_response = requests.get(f"https://environment.data.gov.uk/flood-monitoring/id/stations/{station_id}/measures")
    station_response = requests.get(f"http://127.0.0.1:{PORT}/stations")

    station_data = station_response.json()
    station_name = next((s["name"] for s in station_data if s["id"] == station_id), None)

    parameter, unit = "Unknown Parameter", "Unknown Unit"
    if measurements_response.status_code == 200:
        measurements = measurements_response.json()
        parameter = measurements.get("items", [{}])[0].get("parameter", "Unknown Parameter")
        unit = measurements.get("items", [{}])[0].get("unitName", "Unknown Unit")

    now = datetime.now(pytz.utc)
    last_24_hours = now - timedelta(hours=24)

    if readings_response.status_code == 200:
        readings = readings_response.json()
        df = pd.DataFrame(readings)

        if df.empty:
            return px.line(title="No data available"), "", ""

        df["time"] = pd.to_datetime(df["time"]) 
        df = df[df["time"] >= last_24_hours]  

        if df.empty:
            return px.line(title="No readings in the last 24 hours"), "", ""

        df.sort_values(by="time", inplace=True)
        if df.index.name == "time":
            df = df.reset_index()

        fig = px.line(df, x="time", y="value", title=f"Readings for {station_name} (ID: {station_id})", 
                      labels={"value": f"{parameter} ({unit})", "time": "Time"})

        table = DataTable(
            columns=[{"name": col, "id": col} for col in df.columns],
            data=df.to_dict("records"),
            style_table={'overflowX': 'auto'},
            style_cell={'textAlign': 'left'}
        )

        # Extract flood warnings
        warning_message = "No active flood warnings for this station."
        if warnings_response.status_code == 200:
            flood_data = warnings_response.json()
            for warning in flood_data.get("items", []):
                area_name = warning.get("eaAreaName", "").lower()
                warning_details = warning.get("message", "No details available")

                if station_name and station_name.lower() in area_name:
                    warning_message = f"{warning_details}"
                    print(f"DEBUG: Matched Flood Warning for {station_id} - {warning_details}")
                    break

        return fig, html.Div([html.H3("Data Table"), table]), html.Div([html.H3("Flood Risk"), html.P(warning_message)])

    return px.line(title="No data available"), "", ""


if __name__ == "__main__":
    server.run(debug=True, host="0.0.0.0", port=PORT)
