// The dependency is renamed in Cargo. This alias also lets macro expansions that use the
// defining crate name compile; discovery must use the resolved dependency identity.
extern crate framework as suprnova;

use framework::model;

#[model(table = "posts")]
pub struct Post {
    pub id: i64,
    pub title: String,
}

#[model(table = "other_posts")]
pub struct Other {
    pub id: i64,
    pub title: String,
}

// A short trait name alone must never turn this unrelated owner into a Suprnova model.
pub mod unrelated {
    pub trait Model {}
    pub struct Post;
    impl Model for Post {}
}

pub fn inspect() {
    use framework::eloquent::Model as _;
    let automatic_post = Post::query();
    let automatic_other = Other::query();
    let automatic_column = post::Column::Title;
    let automatic_unrelated = unrelated::Post;
    let automatic_source = 7u64;
    let _ = (
        automatic_post,
        automatic_other,
        automatic_column,
        automatic_unrelated,
        automatic_source,
    );
}
