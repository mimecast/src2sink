package com.example.fulfilment.dao;

import org.springframework.jdbc.core.JdbcTemplate;

/**
 * OI-8 regression fixture: SQL assembled by formatting and by concatenation
 * containing an embedded quote. Both constructions produced no `sql` source node
 * at all before the pattern rewrite, so a confirmed injection was invisible.
 */
public class StockQueryBuilder {

    private final JdbcTemplate jdbcTemplate;

    public StockQueryBuilder(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    String byReference(String ref) {
        return String.format("SELECT * FROM stock WHERE ref = '%s'", ref);
    }

    String byLocation(String location) {
        return "SELECT * FROM stock WHERE location = '" + location + "'";
    }

    List<Stock> run(String ref) {
        return jdbcTemplate.query(byReference(ref), mapper);
    }
}
