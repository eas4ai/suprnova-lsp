extern crate framework as suprnova;

use framework::model;

#[model(table = "binary_posts")]
pub struct BinaryPost {
    pub id: i64,
    pub title: String,
}

fn main() {
    use framework::eloquent::Model as _;
    let automatic_binary = BinaryPost::query();
    let _ = automatic_binary;
}
