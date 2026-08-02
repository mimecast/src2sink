package com.example.fulfilment.proxy;

import org.apache.http.client.methods.HttpUriRequest;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * OI-7 regression fixture: an HTTP proxy that does no SQL at all.
 *
 * It carries a field named `sql`, calls `httpClient.execute(...)` and
 * `messageDigest.update(...)`, and must therefore produce neither a `sql` node
 * nor a `raw-code-payload` node. Before the receiver/evidence gate it produced
 * both, at `high` confidence.
 */
@RestController
public class StockForwarder {

    private String sql;

    private final HttpClientWrapper httpClient;
    private final MessageDigest messageDigest;

    public StockForwarder(HttpClientWrapper httpClient, MessageDigest messageDigest) {
        this.httpClient = httpClient;
        this.messageDigest = messageDigest;
    }

    @PostMapping("/v1/forward")
    public Response forward(@RequestBody StockRequest request) throws Exception {
        messageDigest.update(request.checksumBytes());
        return httpClient.execute(request.toHttpRequest());
    }
}
