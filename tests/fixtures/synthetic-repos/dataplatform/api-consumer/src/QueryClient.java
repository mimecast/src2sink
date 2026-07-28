package com.example.client;

import java.net.URI;
import java.net.http.HttpRequest;

public class QueryClient {
    public HttpRequest buildRequest(String sql) {
        return HttpRequest.newBuilder(
            URI.create("https://query-api-service.dev/v1/queries")
        ).POST(HttpRequest.BodyPublishers.ofString("{\"sql\":\"" + sql + "\"}")).build();
    }
}
