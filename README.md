# TrainTripper: ML-Enhanced Predictive Ticket Cost Estimation and Smart Train Selection

## Table of Contents
- [Description](#description)
- [Installation](#installation)
- [Usage](#usage)

## Description
Finding and booking indirect train journeys in India can be challenging, especially for long trips or remote destinations. Existing platforms only offer direct trains, which may not be available or convenient for some travelers. This project aims to address this gap by developing a software application that can generate and compare hundreds of options for indirect trains, as well as direct ones. The application uses web scraping, search algorithms, and machine learning to collect, process, and estimate the data from the IRCTC website. Users can sort and filter the results by various criteria, such as estimated price and duration.

![System Design](./Combined/system%20design.png)

This one image encompasses all the blocks of this project. The overall goal of this project is basically to use the 
existing platforms to collect raw data, format the data to create an algorithm to find the trains, use ML model to 
estimate the prices and combine all those in a file and present a nice output to the user. You can find more details 
in the Project Report document. 

## Installation
To use this project, make sure you have Python installed on your system. Additionally, install the required packages listed in the `requirements.txt` file. You can install them using the following command:

```bash
pip install -r requirements.txt
```

## Usage

1> Navigate to the Combined directory in your terminal.
```bash
cd Combined
```
2> Open the main.py file, locate line 117 and make necessary changes like changing the source station, destination 
station and date according to your requirements.

3> Run the following command to execute the program:
```bash
python main.py
```

4> You might see some warnings but ignore those and find the `direct_trains.csv` and `indirect_trains.csv` files in the 
same directory which is the final output of the code. You can understand the column details from their names but you 
can find more details in the Project Report document.