# Portfolio Monitoring Pack — loan-level (Freddie Mac SFLLD)

_Real loan-level mortgage data. The monitoring mechanics apply equally to commercial loan portfolios with a monthly status feed._

## 1. Monthly delinquency-bucket transition matrix

| bucket   |   Current |     30 |     60 |    90+ |   Default |   Prepaid |
|:---------|----------:|-------:|-------:|-------:|----------:|----------:|
| Current  |    0.975  | 0.0099 | 0.0001 | 0.0001 |    0      |    0.0149 |
| 30       |    0.3282 | 0.4647 | 0.2054 | 0.0017 |    0      |    0      |
| 60       |    0.1094 | 0.1428 | 0.3552 | 0.3774 |    0.0002 |    0.0149 |
| 90+      |    0.0462 | 0.0066 | 0.0131 | 0.9038 |    0.0269 |    0.0034 |

![heatmap](../charts/02_bucket_transition_heatmap.png)

## 2. Headline roll rates

| roll_rate                             |   monthly_probability |
|:--------------------------------------|----------------------:|
| Current -> 30 (new delinquency)       |                0.0099 |
| 30 -> 60 (roll worse)                 |                0.2054 |
| 60 -> 90+ (roll worse)                |                0.3774 |
| 90+ -> Default (roll to credit event) |                0.0269 |
| 30 -> Current (cure)                  |                0.3282 |
| 60 -> Current/30 (cure)               |                0.2523 |
| Current -> Prepaid (voluntary exit)   |                0.0149 |

## 3. IFRS 9 stage movements (loan-months)

| move                          |   loan_months |   share |
|:------------------------------|--------------:|--------:|
| 1 -> 1  stay performing       |       8124369 |  0.9198 |
| 3 -> 3  stay defaulted        |        250832 |  0.0284 |
| 2 -> 2  stay watch            |        145608 |  0.0165 |
| 1 -> exit (prepaid)           |        124177 |  0.0141 |
| 1 -> 2  deteriorate (SICR)    |         83246 |  0.0094 |
| 2 -> 1  cure                  |         63194 |  0.0072 |
| 2 -> 3  deteriorate (default) |         23073 |  0.0026 |
| 3 -> 1  cure                  |         10873 |  0.0012 |
| 3 -> 2  partial cure          |          4645 |  0.0005 |
| 2 -> exit (prepaid)           |           899 |  0.0001 |
| 3 -> exit (prepaid)           |           807 |  0.0001 |
| 1 -> 3  deteriorate (default) |           632 |  0.0001 |

## 4. Early-warning watchlist (by vintage / stage)

|   vintage | stage   |   loans |   exposure_upb |
|----------:|:--------|--------:|---------------:|
|      2007 | Stage 2 |      57 |    5.60921e+06 |
|      2007 | Stage 3 |      28 |    3.26667e+06 |
|      2008 | Stage 2 |      39 |    3.68731e+06 |
|      2008 | Stage 3 |      17 |    2.34108e+06 |
|      2015 | Stage 2 |     155 |    2.37496e+07 |
|      2015 | Stage 3 |      56 |    9.71566e+06 |

## 5. Vintage tracking — cumulative default by months on book

|   months_on_book |   2007_cum_default_pct |   2008_cum_default_pct |   2015_cum_default_pct |
|-----------------:|-----------------------:|-----------------------:|-----------------------:|
|               12 |                   2.45 |                   1.89 |                   0.3  |
|               24 |                   6.18 |                   4.47 |                   0.68 |
|               36 |                   9.86 |                   6.03 |                   1.02 |
|               48 |                  12.2  |                   7.19 |                   1.23 |
|               60 |                  13.74 |                   7.84 |                   2.7  |
|               72 |                  14.64 |                   8.2  |                   3.65 |

![vintage curves](../charts/05_vintage_default_curves.png)

## 6. Concentration by state (top 10) — APS 330-style format

_Format only — illustrative, not a regulatory submission._

| prop_state   |   loans |   exposure_upb |   pct_90plus |   exposure_share_pct |
|:-------------|--------:|---------------:|-------------:|---------------------:|
| CA           |    1727 |    2.99597e+08 |         0.46 |                17.26 |
| NY           |     820 |    1.27007e+08 |         0.85 |                 7.32 |
| FL           |     955 |    1.09501e+08 |         0.94 |                 6.31 |
| TX           |    1017 |    1.03262e+08 |         0.69 |                 5.95 |
| IL           |     697 |    7.43951e+07 |         1.15 |                 4.29 |
| VA           |     451 |    6.17589e+07 |         0.22 |                 3.56 |
| NJ           |     428 |    6.13502e+07 |         0.47 |                 3.53 |
| PA           |     587 |    5.88162e+07 |         0.68 |                 3.39 |
| MA           |     336 |    5.15798e+07 |         0.6  |                 2.97 |
| GA           |     470 |    4.90373e+07 |         0.64 |                 2.83 |
