package com.example.fulfilment.commons;

import com.example.fulfilment.commons.transport.ApiClient;

/**
 * OI-2 regression fixture: a caller that reaches the service through an in-house
 * REST abstraction. No Spring or JDK HTTP type is named anywhere — the HTTP
 * concern lives in the transport module — so the file-level guard rejected the
 * call site and this caller was invisible to the graphs.
 */
public class StockRequestProcessor {

    private static final String STOCK_SUBMIT_URL = "/v1/stock";

    private final ApiClient client;

    public StockRequestProcessor(ApiClient client) {
        this.client = client;
    }

    public StockSubmitResponse submit(StockRequest request) {
        return client.post(STOCK_SUBMIT_URL, request, StockSubmitResponse.class);
    }
}
