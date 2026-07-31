package main

import (
	"net/http"
)

func callSqlRunnerApi() {
	http.Get("https://sql-runner-api.example/v1/queries")
}
