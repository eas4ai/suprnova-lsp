use proc_macro::TokenStream;

#[proc_macro_attribute]
pub fn make_model(_: TokenStream, input: TokenStream) -> TokenStream {
    let declarations: TokenStream = r#"
        pub mod generated {
            pub struct Storage { pub id: i64 }
            pub struct Entity;
            pub enum Column { Id, Email }
        }
        impl Model for Post {
            type Key = i64;
            type Entity = generated::Entity;
            type Column = generated::Column;
        }
        impl Bridge<generated::Storage> for Post {
            fn bridge(value: generated::Storage) -> Self { Self { id: value.id as u64 } }
        }
        impl Bridge<Post> for generated::Storage {
            fn bridge(value: Post) -> Self { Self { id: value.id as i64 } }
        }
    "#.parse().expect("fixture declarations parse");
    input.into_iter().chain(declarations).collect()
}
