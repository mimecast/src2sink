package com.example.query;

import org.springframework.web.bind.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;

@RestController
@RequestMapping("/queries")
public class QueryController {
    private final JdbcTemplate jdbcTemplate;

    public QueryController(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @PostMapping
    public Object execute(@RequestBody QueryRequest request) {
        return jdbcTemplate.query(request.getSql());
    }

    static class QueryRequest {
        private String sql;
        public String getSql() { return sql; }
    }
}
