package com.example.fulfilment.dao

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController

/**
 * The Kotlin twin of fulfilment/stock-dao's StockQueryBuilder.
 *
 * The corpus carried no Kotlin at all, which is why the snapshots could not have
 * caught OI-13: the AST pass named Kotlin in CALL_NODE_TYPES, routed it to the
 * Java walker, and produced nothing — and no fixture exercised the language.
 */
@RestController
class StockQueryBuilder(private val jdbcTemplate: JdbcTemplate) {

    @PostMapping("/stock/search")
    fun search(@RequestBody filter: String): List<Stock> =
        jdbcTemplate.query("SELECT ref FROM stock WHERE label = '" + filter + "'", mapper)

    fun parameterised(ref: String): List<Stock> =
        jdbcTemplate.query("SELECT ref FROM stock WHERE ref = ?", mapper, ref)

    fun notADatabase(request: Request): Response = httpClient.execute(request)
}
