package main

import (
	"net/http"
)

func callQueryAPI() {
	http.Get("https://query-api-service.example/v1/queries")
}
