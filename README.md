# TrainTripper: ML-Enhanced Predictive Ticket Cost Estimation and Smart Train Selection

## Table of Contents
- [Description](#description)
- [Installation](#installation)
- [Usage](#usage)
- [Scope](#scope)

## Description
Finding and booking indirect train journeys in India can be challenging, especially for long trips or remote destinations. Existing platforms only offer direct trains, which may not be available or convenient for some travelers. This project aims to address this gap by developing a software application that can generate and compare hundreds of options for indirect trains, as well as direct ones. The application uses web scraping, search algorithms, and machine learning to collect, process, and estimate the data from the IRCTC website. Users can sort and filter the results by various criteria, such as estimated price and duration.

![System Design](./Combined/system%20design.jpg)

This one image encompasses all the blocks of this project. The overall goal of this project is basically to use the 
existing platforms to collect raw data, format the data to create an algorithm to find the trains, use ML model to 
estimate the prices and combine all those in a file and present a nice output to the user. You can find more details 
in the Project Report document. 

## Installation
1. Clone the repository:

```bash
git clone https://github.com/username/WeatherForecastApp.git
```

2. Make sure you have Python installed on your system. Preferred version of Python is 3.11

3. Install the required packages listed in the `requirements.txt` file. You can install them using the following 
command:

```bash
pip install -r requirements.txt
```

## Usage

1. Navigate to the Combined directory in your terminal.
```bash
cd Combined
```
2. Open the main.py file, locate line 117 and make necessary changes like changing the source station, destination 
station and date according to your requirements.

3. Run the following command to execute the program:
```bash
python main.py
```

4. You might see some warnings but ignore those and find the `direct_trains.csv` and `indirect_trains.csv` files in the 
same directory which is the final output of the code. You can understand the column details from their names but you 
can find more details in the Project Report document.

## Scope

1. Use my dataset of this project uploaded on [Kaggle](https://www.kaggle.com/datasets/bhavyarajdev/indian-railways-schedule-prices-availability-data/) to perform time series analysis on price and availability data
2. Use the output of my code which is CSV to display on web-application where user can input the from-to station and 
dates to find the direct and indirect trains between those stations on given date along with getting user feedbacks and improving.
3. Though the data is collected during october-2023, the train data keeps updating on IRCTC. One can write scripts 
   to regularly refresh the data used for train search in the project