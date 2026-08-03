package com.example.fulfilment.commons;

/**
 * OI-9 regression fixture: this repo ships arbitrary SQL to another service.
 *
 * Before the sql-payload-out family existed this was an ordinary http-out and
 * nothing more — not a local sql sink (nothing executes here) and not a
 * raw-code-payload (that family is inbound), so the hop's sending end was
 * unrepresented.
 */
public class StockQueryForwarder {

    private static final String SUBMIT_URL = "/v1/query";

    private final RestTemplate restTemplate;

    public StockQueryForwarder(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public QueryResult submit(String sqlText) {
        QueryRequest body = new QueryRequest();
        body.setSql(sqlText);
        return restTemplate.postForObject(SUBMIT_URL, body, QueryResult.class);
    }
}
