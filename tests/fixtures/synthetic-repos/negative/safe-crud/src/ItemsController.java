package com.example.items;

import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
public class ItemsController {
    @GetMapping("/items")
    public List<Item> list() {
        return repository.findAll();
    }
}
